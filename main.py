"""FastAPI entrypoint: health endpoint plus Telegram and sensor webhook routers."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI

from api.telegram_gateway import TelegramGateway, TelegramUpdate
from api.telegram_gateway import router as telegram_router
from api.telegram_sender import TelegramSender
from api.webhooks import router as webhook_router
from core.budget_governor import BudgetGovernor, SqliteBudgetStore
from core.cognitive_ledger import KILL_SWITCH_KEY, CognitiveLedger
from core.config_loader import (
    AppConfig,
    SecretNotFoundError,
    SecretsManager,
    load_config,
)
from core.llm_worker import OpenRouterWorker
from core.runtime import Runtime, load_plugins, scheduler_loop

Responder = Callable[[TelegramUpdate, str], Awaitable[str]]

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
    )


def build_runtime(
    *,
    settings: AppConfig,
    ledger: CognitiveLedger,
    governor: BudgetGovernor,
    gateway: TelegramGateway,
    worker: Optional[Callable[[str], Awaitable[str]]] = None,
    propose_patch=None,
    plugins_dir: Optional[str | Path] = None,
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
        secrets = SecretsManager()
        worker = OpenRouterWorker(
            secrets.get_secret("OPENROUTER_API_KEY"),
            settings.models.tier_2_heavy,
        )
    return Runtime(
        ledger,
        governor,
        plugins=plugins,
        worker=worker,
        propose_patch=propose_patch,
        wake_up=gateway.wake_up_ping,
        input_usd_per_million=settings.pricing.input_usd_per_million,
        output_usd_per_million=settings.pricing.output_usd_per_million,
    )


def create_production_app(
    config_path: Optional[str | Path] = None,
    scheduler_interval: float = 60.0,
) -> FastAPI:
    """Fully wired app: configured gateway, LLM worker, plugins, scheduler."""
    settings = load_config(config_path or _DEFAULT_CONFIG)
    ledger = CognitiveLedger(settings.sqlite_path)
    governor = BudgetGovernor(
        settings.budget.max_initiative_budget_usd,
        settings.budget.core_task_ratio,
        settings.budget.atelier_ratio,
        settings.rate_limits.rpm_limit,
        settings.rate_limits.tpm_limit,
        settings.rate_limits.throttle_threshold_ratio,
        store=SqliteBudgetStore(settings.sqlite_path),
    )
    gateway = build_gateway(settings)
    runtime = build_runtime(
        settings=settings, ledger=ledger, governor=governor, gateway=gateway
    )
    return create_app(
        settings=settings,
        ledger=ledger,
        governor=governor,
        gateway=gateway,
        runtime=runtime,
        scheduler_interval=scheduler_interval,
        config_path=config_path,
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
) -> FastAPI:
    settings = settings or load_config(config_path or _DEFAULT_CONFIG)
    ledger = ledger or CognitiveLedger(settings.sqlite_path)
    governor = governor or BudgetGovernor(
        settings.budget.max_initiative_budget_usd,
        settings.budget.core_task_ratio,
        settings.budget.atelier_ratio,
        settings.rate_limits.rpm_limit,
        settings.rate_limits.tpm_limit,
        settings.rate_limits.throttle_threshold_ratio,
        store=SqliteBudgetStore(settings.sqlite_path),
    )
    gateway = gateway or TelegramGateway(workspace_dir=settings.paths.workspace_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await ledger.initialize()
        gateway.wake_up_ping()
        scheduler_task = None
        if runtime is not None:
            scheduler_task = asyncio.create_task(
                scheduler_loop(
                    runtime,
                    60.0 if scheduler_interval is None else scheduler_interval,
                )
            )
        try:
            yield
        finally:
            if scheduler_task is not None:
                scheduler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduler_task

    app = FastAPI(title="devi-subhadra", lifespan=lifespan)
    app.state.settings = settings
    app.state.ledger = ledger
    app.state.governor = governor
    app.state.gateway = gateway
    app.state.runtime = runtime
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
        return {
            "status": "healthy" if database_ok else "degraded",
            "execution_mode": app.state.settings.execution_mode,
            "rpm_used": app.state.governor.rpm_used,
            "rpm_limit": app.state.governor.rpm_limit,
            "budget_remaining_usd": app.state.governor.remaining_usd("CORE_70")
            + app.state.governor.remaining_usd("ATELIER_30"),
            "database": "ok" if database_ok else "error",
            "kill_switch_active": await app.state.ledger.get_system_state(
                KILL_SWITCH_KEY
            )
            == "1",
        }

    return app