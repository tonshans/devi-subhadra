"""FastAPI entrypoint: health endpoint plus Telegram and sensor webhook routers."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI

from api.telegram_gateway import TelegramGateway, TelegramUpdate
from api.telegram_gateway import polling_loop
from api.telegram_gateway import router as telegram_router
from api.telegram_sender import TelegramSender
from api.webhooks import router as webhook_router
from core.backup import backup_loop
from core.budget_governor import BudgetGovernor, SqliteBudgetStore
from core.cognitive_ledger import KILL_SWITCH_KEY, CognitiveLedger
from core.commands import CommandContext, load_governor_overrides, status
from core.config_loader import (
    AppConfig,
    SecretNotFoundError,
    SecretsManager,
    load_config,
)
from core.db.engine import DatabaseRuntime, build_database
from core.db.timestamps import utc_now_text
from core.llm_worker import GeminiWorker, OpenRouterWorker
from core.provider_pool import ProviderKey, ProviderPool
from core.runtime import Runtime, load_plugins, scheduler_loop

Responder = Callable[[TelegramUpdate, str], Awaitable[str]]
logger = logging.getLogger(__name__)
_LOGGING_CONFIGURED = False

# Anchor to the repo, not the process CWD; uvicorn may start anywhere.
_DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")

# Telegram puts the bot token in the URL, so never let httpx log requests.
_NOISY_LOGGERS = ("httpx", "httpcore", "chromadb", "sqlalchemy.engine")


def polling_enabled() -> bool:
    return os.environ.get("TELEGRAM_POLLING", "0").lower() in {"1", "true", "yes"}


def configure_logging(level: str | int | None = None) -> None:
    """Terminal-only logging; idempotent so uvicorn and __main__ can both call."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    _LOGGING_CONFIGURED = True


async def _default_responder(update: TelegramUpdate, thread_id: str) -> str:
    return f"Mission {thread_id} accepted."


def build_gateway(settings: AppConfig) -> TelegramGateway:
    """Configured gateway: bot token + explicit chat allowlist, fail closed."""
    secrets = SecretsManager()
    return TelegramGateway(
        sender=TelegramSender(secrets.get_secret("TELEGRAM_BOT_TOKEN")),
        workspace_dir=settings.paths.workspace_dir,
        allowed_chat_id=int(secrets.get_secret("TELEGRAM_ALLOWED_CHAT_ID")),
        atelier_dir=settings.paths.atelier_dir,
    )


def build_governor(settings: AppConfig, store=None) -> BudgetGovernor:
    """Governor with the configured provider limits and agent share."""
    return BudgetGovernor(
        settings.budget.max_initiative_budget_usd,
        settings.budget.core_task_ratio,
        settings.budget.atelier_ratio,
        settings.rate_limits.rpm_limit,
        settings.rate_limits.tpm_limit,
        settings.rate_limits.throttle_threshold_ratio,
        store=store,
        rpd_limit=settings.rate_limits.rpd_limit,
        tpd_limit=settings.rate_limits.tpd_limit,
        budget_ratio_agent=settings.rate_limits.budget_ratio_agent,
        agent_freetime=settings.rate_limits.agent_freetime,
    )


def build_worker(
    settings: AppConfig, governor: BudgetGovernor
) -> Callable[[str], Awaitable[object]]:
    """Configured LLM worker: a Gemini key pool, or one OpenRouter key."""
    secrets = SecretsManager()
    if settings.rate_limits.provider != "gemini":
        secret = secrets.get_secret("OPENROUTER_API_KEY")
        key = ProviderKey(id="openrouter", secret=secret)
        return ProviderPool(
            [key],
            lambda _: OpenRouterWorker(
                secret, settings.models.tier_2_heavy, structured=True
            ),
            governor,
            provider="openrouter",
        )
    keys: list[ProviderKey] = []
    for key in settings.gemini_keys:
        try:
            secret = secrets.get_secret(key.env)
        except SecretNotFoundError:
            logger.warning(
                "gemini key %s skipped: %s is not set", key.id, key.env
            )
            continue
        keys.append(
            ProviderKey(
                id=key.id,
                secret=secret,
                rpm_limit=key.rpm_limit,
                tpm_limit=key.tpm_limit,
                rpd_limit=key.rpd_limit,
                tpd_limit=key.tpd_limit,
            )
        )
    if not keys:
        raise SecretNotFoundError(
            settings.gemini_keys[0].env if settings.gemini_keys else "GEMINI_API_KEY"
        )
    return ProviderPool(
        keys,
        lambda key: GeminiWorker(
            key.secret, settings.models.tier_2_heavy, structured=True
        ),
        governor,
    )


def build_runtime(
    *,
    settings: AppConfig,
    ledger: CognitiveLedger,
    governor: BudgetGovernor,
    gateway: TelegramGateway,
    worker: Optional[Callable[[str], Awaitable[object]]] = None,
    propose_patch=None,
    plugins_dir: Optional[str | Path] = None,
    deliver_file=None,
) -> Runtime:
    """Production composition root: plugins, LLM worker, wake-up ping."""
    plugin_dir = Path(plugins_dir or Path(__file__).resolve().parent / "plugins")
    archived_dir = Path(settings.paths.atelier_dir) / "archived_plugins"
    plugin_config = {
        "paths": {
            "workspace_dir": settings.paths.workspace_dir,
            "atelier_dir": settings.paths.atelier_dir,
        },
        "plugins_dir": str(plugin_dir),
        "archived_plugins_dir": str(archived_dir),
        "ledger": ledger,
    }
    plugins, catalog = load_plugins(
        plugin_dir, plugin_config, core_plugins=settings.core_plugins
    )
    if worker is None:
        worker = build_worker(settings, governor)
    return Runtime(
        ledger,
        governor,
        plugins=plugins,
        worker=worker,
        propose_patch=propose_patch,
        wake_up=gateway.wake_up_ping,
        monologue_every_cycles=settings.monologue_every_cycles,
        curiosity_daily_limit=settings.curiosity_daily_limit,
        audit_enabled=settings.audit_enabled,
        halt_dir=settings.halt_dir,
        workspace_dir=settings.paths.workspace_dir,
        input_usd_per_million=settings.pricing.input_usd_per_million,
        output_usd_per_million=settings.pricing.output_usd_per_million,
        deliver_file=deliver_file,
        notify=getattr(gateway, "notify", None),
        plugin_catalog=catalog,
        plugins_dir=plugin_dir,
        plugin_config=plugin_config,
        archived_plugins_dir=archived_dir,
    )


async def _telegram_webhook_info(gateway: TelegramGateway) -> Optional[dict]:
    """getWebhookInfo tells us whether Telegram can actually reach this host."""
    fetch = getattr(gateway, "webhook_info", None)
    if fetch is None:
        return None
    try:
        return await asyncio.wait_for(fetch(), timeout=10)
    except Exception:
        logger.warning("telegram getWebhookInfo failed", exc_info=True)
        return None


async def _startup_notice(
    *,
    settings: AppConfig,
    ledger: CognitiveLedger,
    governor: BudgetGovernor,
    gateway: TelegramGateway,
    runtime: Optional[Runtime],
    info: Optional[dict],
) -> None:
    """Push one status message after boot; failures must never abort startup."""
    if not gateway.is_configured:
        return
    try:
        context = CommandContext(
            ledger, governor, settings, provider=getattr(runtime, "provider", None)
        )
        lines = [f"🟢 {settings.agent_name} online", "", await status(context, "")]
        lines.append(f"inbound: {'polling' if polling_enabled() else 'webhook'}")
        if info is not None:
            lines.append(
                f"webhook: {info.get('url') or 'NOT SET'} "
                f"(pending: {info.get('pending_update_count', 0)})"
            )
            if info.get("last_error_message"):
                lines.append(f"webhook error: {info['last_error_message']}")
        await asyncio.wait_for(gateway.notify("\n".join(lines)), timeout=10)
        logger.info("startup notice sent to telegram")
    except Exception:
        logger.warning("startup notice failed", exc_info=True)


def create_production_app(
    config_path: Optional[str | Path] = None,
    scheduler_interval: float = 60.0,
) -> FastAPI:
    """Fully wired app: configured gateway, LLM worker, plugins, scheduler."""
    configure_logging()
    settings = load_config(config_path or _DEFAULT_CONFIG)
    database = build_database(settings)
    ledger = CognitiveLedger(engine=database.engine)
    governor = build_governor(settings, store=database.budget_store())
    gateway = build_gateway(settings)
    runtime = build_runtime(
        settings=settings,
        ledger=ledger,
        governor=governor,
        gateway=gateway,
        deliver_file=gateway.send_file,
    )
    return create_app(
        settings=settings,
        ledger=ledger,
        governor=governor,
        gateway=gateway,
        runtime=runtime,
        scheduler_interval=scheduler_interval,
        config_path=config_path,
        database=database,
        startup_notice=True,
    )


def create_app(
    *,
    settings: Optional[AppConfig] = None,
    ledger: Optional[CognitiveLedger] = None,
    governor: Optional[BudgetGovernor] = None,
    gateway: Optional[TelegramGateway] = None,
    responder: Optional[Responder] = None,
    webhook_token: Optional[str] = None,
    config_path: Optional[str | Path] = None,
    runtime: Optional[Runtime] = None,
    scheduler_interval: Optional[float] = None,
    webhook_tokens: Optional[dict[str, str]] = None,
    database: Optional[DatabaseRuntime] = None,
    startup_notice: bool = False,
) -> FastAPI:
    settings = settings or load_config(config_path or _DEFAULT_CONFIG)
    ledger = ledger or (
        CognitiveLedger(engine=database.engine)
        if database is not None
        else CognitiveLedger(settings.sqlite_path)
    )
    governor = governor or build_governor(
        settings,
        store=(
            database.budget_store()
            if database is not None
            else SqliteBudgetStore(settings.sqlite_path)
        ),
    )
    gateway = gateway or TelegramGateway(
        workspace_dir=settings.paths.workspace_dir,
        atelier_dir=settings.paths.atelier_dir,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await ledger.initialize()
        await load_governor_overrides(ledger, governor)
        if runtime is not None:
            await runtime.sync_plugin_registry()
        if database is not None and database.fallback:
            await ledger.set_system_state("fallback_since", utc_now_text())
        if polling_enabled():
            try:
                await gateway.delete_webhook()
                logger.info("telegram polling: webhook removed")
            except Exception:
                logger.warning("telegram deleteWebhook failed", exc_info=True)
        info = await _telegram_webhook_info(gateway)
        if info is not None:
            logger.info(
                "telegram webhook: url=%s pending=%s last_error=%s",
                info.get("url") or "NOT SET",
                info.get("pending_update_count", 0),
                info.get("last_error_message") or "none",
            )
        if startup_notice and os.environ.get("AGENT_STARTUP_NOTICE", "1") != "0":
            await _startup_notice(
                settings=settings,
                ledger=ledger,
                governor=governor,
                gateway=gateway,
                runtime=runtime,
                info=info,
            )
        gateway.wake_up_ping()
        logger.info(
            "startup complete: db=%s fallback=%s scheduler=%ss",
            database.backend if database else ledger.dialect,
            bool(database and database.fallback),
            60.0 if scheduler_interval is None else scheduler_interval,
        )
        tasks = []
        if runtime is not None:
            tasks.append(
                asyncio.create_task(
                    scheduler_loop(
                        runtime,
                        60.0 if scheduler_interval is None else scheduler_interval,
                    )
                )
            )
        if polling_enabled():
            tasks.append(asyncio.create_task(polling_loop(app.state)))
        if (
            database is not None
            and database.is_postgres
            and settings.backup.enabled
        ):
            tasks.append(
                asyncio.create_task(
                    backup_loop(
                        database.engine,
                        settings.backup.sqlite_path,
                        settings.backup.interval_seconds,
                    )
                )
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="devi-subhadra", lifespan=lifespan)
    app.state.settings = settings
    app.state.ledger = ledger
    app.state.governor = governor
    app.state.gateway = gateway
    app.state.runtime = runtime
    app.state.database = database
    app.state.command_context = CommandContext(
        ledger, governor, settings, provider=getattr(runtime, "provider", None)
    )
    app.state.db_backend = database.backend if database else ledger.dialect
    app.state.db_fallback = bool(database and database.fallback)
    app.state.responder = responder or _default_responder
    if webhook_token is None:
        try:
            webhook_token = SecretsManager().get_secret("AGENT_WEBHOOK_TOKEN")
        except SecretNotFoundError:
            webhook_token = None
    app.state.webhook_token = webhook_token
    app.state.webhook_tokens = webhook_tokens

    app.include_router(telegram_router)
    app.include_router(webhook_router)

    @app.get("/api/health")
    async def health() -> dict:
        database_ok = await app.state.ledger.ping()
        provider = getattr(app.state.runtime, "provider", None)
        return {
            "status": "healthy" if database_ok else "degraded",
            "execution_mode": app.state.settings.execution_mode,
            "rpm_used": app.state.governor.rpm_used,
            "rpm_limit": app.state.governor.rpm_limit,
            "budget_remaining_usd": app.state.governor.remaining_usd("CORE_70")
            + app.state.governor.remaining_usd("ATELIER_30"),
            "database": "ok" if database_ok else "error",
            "db_backend": app.state.db_backend,
            "db_fallback": app.state.db_fallback,
            "kill_switch_active": await app.state.ledger.get_system_state(
                KILL_SWITCH_KEY
            )
            == "1",
            "provider": app.state.settings.rate_limits.provider,
            "provider_keys_ready": (
                provider.ready_count if provider is not None else None
            ),
        }

    return app


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run devi-subhadra.")
    parser.add_argument(
        "--debug", action="store_true", help="show INFO/WARNING/ERROR/DEBUG logs"
    )
    parser.add_argument("--log-level", default=None, help="override LOG_LEVEL")
    parser.add_argument(
        "--polling", action="store_true", help="read Telegram via getUpdates"
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.polling:
        os.environ["TELEGRAM_POLLING"] = "1"
    level = (
        "DEBUG"
        if args.debug
        else (args.log_level or os.environ.get("LOG_LEVEL", "INFO"))
    )
    configure_logging(level)
    uvicorn.run(
        "main:create_production_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level=level.lower(),
        log_config=None,
        access_log=True,
    )
