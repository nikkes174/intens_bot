import pytest

from tgbot.config import ConfigError, load_config


def test_load_config_reports_missing_openrouter_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "BOT_TOKEN",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_ALLOW_PAID",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_TIMEOUT",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        load_config(None)


def test_load_config_rejects_paid_model_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    monkeypatch.setenv("OPENROUTER_MODEL", "provider/model")
    monkeypatch.delenv("OPENROUTER_ALLOW_PAID", raising=False)

    with pytest.raises(ConfigError, match="OPENROUTER_MODEL"):
        load_config(None)


def test_load_config_allows_explicit_paid_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    monkeypatch.setenv("OPENROUTER_MODEL", "provider/model")
    monkeypatch.setenv("OPENROUTER_ALLOW_PAID", "true")

    settings = load_config(None)

    assert settings.openrouter_allow_paid is True
