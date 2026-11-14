"""Config parser/validator for config.yaml and host credential injection."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

REDACTED = "[REDACTED]"


class ConfigValidationError(Exception):
    """Raised when config.yaml is missing, malformed, or violates the locked spec."""


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BudgetConfig(StrictConfig):
    max_initiative_budget_usd: float = Field(gt=0)
    core_task_ratio: float = Field(ge=0, le=1)
    atelier_ratio: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ratios_sum_to_one(self) -> "BudgetConfig":
        if not math.isclose(self.core_task_ratio + self.atelier_ratio, 1.0, abs_tol=1e-9):
            raise ValueError("core_task_ratio + atelier_ratio must equal 1.0")
        return self


class RateLimitsConfig(StrictConfig):
    provider: Literal["gemini", "openrouter"]
    rpm_limit: int = Field(gt=0)
    tpm_limit: int = Field(gt=0)
    throttle_threshold_ratio: float = Field(gt=0, le=1)
    max_context_tokens: int = Field(gt=0)


class ModelsConfig(StrictConfig):
    tier_1_light: str = Field(min_length=1)
    tier_2_heavy: str = Field(min_length=1)


class PathsConfig(StrictConfig):
    workspace_dir: str = Field(min_length=1)
    atelier_dir: str = Field(min_length=1)


class PricingConfig(StrictConfig):
    # Zero means "no real provider rates": legacy flat cost_usd stays in charge.
    input_usd_per_million: float = Field(default=0.0, ge=0)
    output_usd_per_million: float = Field(default=0.0, ge=0)


class AppConfig(StrictConfig):
    agent_name: str = Field(min_length=1)
    execution_mode: Literal["lxc", "local"]
    db_type: Literal["sqlite", "postgres"]
    sqlite_path: str = Field(min_length=1)
    postgres_dsn: str = Field(min_length=1)
    budget: BudgetConfig
    rate_limits: RateLimitsConfig
    models: ModelsConfig
    paths: PathsConfig
    pricing: PricingConfig = Field(default_factory=PricingConfig)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigValidationError(f"cannot load config file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigValidationError(f"config file {path} must contain a mapping")

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        # Omit input values so a bad postgres_dsn never echoes credentials into logs.
        raise ConfigValidationError(
            f"invalid config in {path}: {exc.errors(include_input=False)}"
        ) from exc


class SecretNotFoundError(Exception):
    """Raised when a requested secret is absent from both .env and the process env."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Secret '{key}' is not set on the host")


class SecretsManager:
    """Runtime credential injector; values are only kept in memory, never logged raw."""

    def __init__(self, env_path: str | Path = ".env") -> None:
        env_file = Path(env_path)
        loaded = dotenv_values(env_file) if env_file.is_file() else {}
        self._file_secrets = {key: value for key, value in loaded.items() if value}
        self._redact_values = set(self._file_secrets.values())

    def get_secret(self, key: str) -> str:
        value = self._file_secrets.get(key) or os.environ.get(key)
        if not value:
            raise SecretNotFoundError(key)
        self._redact_values.add(value)
        return value

    def sanitize_log(self, message: str) -> str:
        for value in sorted(self._redact_values, key=len, reverse=True):
            message = message.replace(value, REDACTED)
        return message