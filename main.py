"""FastAPI entrypoint: health endpoint plus Telegram and sensor webhook routers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI

from api.telegram_gateway import TelegramGateway, TelegramUpdate
from api.telegram_gateway import router as telegram_router
from api.telegram_sender import TelegramSender
from api.webhooks import router as webhook_router
from core.backup import backup_loop
from core.budget_governor import BudgetGovernor, SqliteBudgetStore
from core.cognitive_ledger import KILL_SWITCH_KEY, CognitiveLedger
from core.commands import CommandContext, load_governor_overrides
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

# Anchor to the repo, not the process CWD; uvicorn may start anywhere.
_DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")


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
        return OpenRouterWorker(
            secrets.get_secret("OPENROUTER_API_KEY"),
            settings.models.tier_2_heavy,
            structured=True,
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
    plugins = load_plugins(
        plugins_dir or Path(__file__).resolve().parent / "plugins",
        {
            "paths": {
                "workspace_dir": settings.paths.workspace_dir,
                "atelier_dir": settings.paths.atelier_dir,
            }
        },
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
        input_usd_per_million=settings.pricing.input_usd_per_million,
        output_usd_per_million=settings.pricing.output_usd_per_million,
        deliver_file=deliver_file,
        notify=getattr(gateway, "notify", None),
    )


def create_production_app(
    config_path: Optional[str | Path] = None,
    scheduler_interval: float = 60.0,
) -> FastAPI:
    """Fully wired app: configured gateway, LLM worker, plugins, scheduler."""
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
    async def lifespan(_: FastAPI):
        await ledger.initialize()
        await load_governor_overrides(ledger, governor)
        if database is not None and database.fallback:
            await ledger.set_system_state("fallback_since", utc_now_text())
        gateway.wake_up_ping()
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