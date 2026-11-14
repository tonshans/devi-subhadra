"""Unit tests for config.yaml validation and host credential isolation."""

import os

import pytest
import yaml

from core.config_loader import (
    AppConfig,
    ConfigValidationError,
    SecretsManager,
    SecretNotFoundError,
    load_config,
)


def _valid_config() -> dict:
    return {
        "agent_name": "devi-subhadra",
        "execution_mode": "lxc",
        "db_type": "sqlite",
        "sqlite_path": "./data/devi_subhadra.db",
        "postgres_dsn": "postgresql+asyncpg://user:pass@localhost:5432/devi_subhadra",
        "budget": {
            "max_initiative_budget_usd": 2.50,
            "core_task_ratio": 0.70,
            "atelier_ratio": 0.30,
        },
        "rate_limits": {
            "provider": "openrouter",
            "rpm_limit": 60,
            "tpm_limit": 120000,
            "throttle_threshold_ratio": 0.90,
            "max_context_tokens": 30000,
        },
        "models": {
            "tier_1_light": "google/gemini-2.5-flash",
            "tier_2_heavy": "anthropic/claude-3.7-sonnet",
        },
        "paths": {
            "workspace_dir": "./mnt/workspace",
            "atelier_dir": "./mnt/agent_atelier",
        },
    }


def _write_config(tmp_path, data) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


def _write_env(tmp_path, content: str) -> str:
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_load_config_parses_locked_spec(tmp_path):
    config = load_config(_write_config(tmp_path, _valid_config()))

    assert isinstance(config, AppConfig)
    assert config.agent_name == "devi-subhadra"
    assert config.execution_mode == "lxc"
    assert config.db_type == "sqlite"
    assert config.budget.max_initiative_budget_usd == 2.50
    assert config.budget.core_task_ratio == 0.70
    assert config.budget.atelier_ratio == 0.30
    assert config.rate_limits.provider == "openrouter"
    assert config.rate_limits.rpm_limit == 60
    assert config.rate_limits.tpm_limit == 120000
    assert config.rate_limits.throttle_threshold_ratio == 0.90
    assert config.rate_limits.max_context_tokens == 30000
    assert config.models.tier_1_light == "google/gemini-2.5-flash"
    assert config.models.tier_2_heavy == "anthropic/claude-3.7-sonnet"
    assert config.paths.workspace_dir == "./mnt/workspace"
    assert config.paths.atelier_dir == "./mnt/agent_atelier"


def test_pricing_defaults_to_zero_when_absent(tmp_path):
    config = load_config(_write_config(tmp_path, _valid_config()))

    assert config.pricing.input_usd_per_million == 0.0
    assert config.pricing.output_usd_per_million == 0.0


def test_pricing_parses_provider_rates(tmp_path):
    data = _valid_config()
    data["pricing"] = {
        "input_usd_per_million": 0.50,
        "output_usd_per_million": 1.50,
    }

    config = load_config(_write_config(tmp_path, data))

    assert config.pricing.input_usd_per_million == 0.50
    assert config.pricing.output_usd_per_million == 1.50


def test_bundled_config_yaml_matches_spec():
    config = load_config()

    assert config.execution_mode in {"lxc", "local"}
    assert config.db_type in {"sqlite", "postgres"}
    assert config.rate_limits.provider in {"gemini", "openrouter"}


def test_rejects_invalid_enum(tmp_path):
    data = _valid_config()
    data["execution_mode"] = "docker"

    with pytest.raises(ConfigValidationError):
        load_config(_write_config(tmp_path, data))


def test_rejects_budget_ratios_that_do_not_sum_to_one(tmp_path):
    data = _valid_config()
    data["budget"]["core_task_ratio"] = 0.60
    data["budget"]["atelier_ratio"] = 0.20

    with pytest.raises(ConfigValidationError):
        load_config(_write_config(tmp_path, data))


def test_rejects_unknown_keys(tmp_path):
    data = _valid_config()
    data["surprise"] = True

    with pytest.raises(ConfigValidationError):
        load_config(_write_config(tmp_path, data))


def test_rejects_missing_config_file(tmp_path):
    with pytest.raises(ConfigValidationError):
        load_config(str(tmp_path / "does-not-exist.yaml"))


def test_get_secret_returns_host_value(tmp_path):
    env_path = _write_env(tmp_path, "GITHUB_TOKEN=secret123\n")
    manager = SecretsManager(env_path)

    assert manager.get_secret("GITHUB_TOKEN") == "secret123"


def test_get_secret_falls_back_to_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-secret-456")
    manager = SecretsManager(str(tmp_path / "missing.env"))

    assert manager.get_secret("TELEGRAM_BOT_TOKEN") == "env-secret-456"


def test_get_secret_raises_without_leaking_other_secrets(tmp_path):
    env_path = _write_env(tmp_path, "GITHUB_TOKEN=secret123\nOPENROUTER_API_KEY=other789\n")
    manager = SecretsManager(env_path)

    with pytest.raises(SecretNotFoundError) as excinfo:
        manager.get_secret("GEMINI_API_KEY")

    assert "GEMINI_API_KEY" in str(excinfo.value)
    assert "secret123" not in str(excinfo.value)
    assert "other789" not in str(excinfo.value)


def test_sanitize_log_redacts_credentials(tmp_path):
    env_path = _write_env(tmp_path, "GITHUB_TOKEN=secret123\nOPENROUTER_API_KEY=other789\n")
    manager = SecretsManager(env_path)
    manager.get_secret("GITHUB_TOKEN")

    message = manager.sanitize_log("auth failed: token=secret123 key=other789")

    assert message == "auth failed: token=[REDACTED] key=[REDACTED]"
    assert "secret123" not in message
    assert "other789" not in message


def test_sanitize_log_redacts_environment_secret_after_use(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-secret-999")
    manager = SecretsManager(str(tmp_path / "missing.env"))
    manager.get_secret("GEMINI_API_KEY")

    assert manager.sanitize_log("key=env-secret-999") == "key=[REDACTED]"


def test_sanitize_log_leaves_clean_text_untouched(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    manager = SecretsManager(_write_env(tmp_path, "GITHUB_TOKEN=secret123\n"))

    assert manager.sanitize_log("thread active") == "thread active"
    assert os.environ.get("GITHUB_TOKEN") is None  # no process-wide mutation