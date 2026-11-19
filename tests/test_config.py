import pytest

from tgbot.config import ConfigError, load_config


def test_load_config_reports_missing_openrouter_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "BOT_TOKEN",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_TIMEOUT",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        load_config(None)
