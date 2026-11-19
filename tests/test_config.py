import pytest

from tgbot.config import ConfigError, load_config


def test_load_config_reports_missing_openrouter_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "BOT_TOKEN",
        "ADMINS_IDS",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_ALLOW_PAID",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_TIMEOUT",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("ADMINS_IDS", "123456789")
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        load_config(None)


def test_load_config_rejects_paid_model_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("ADMINS_IDS", "123456789")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    monkeypatch.setenv("OPENROUTER_MODEL", "provider/model")
    monkeypatch.delenv("OPENROUTER_ALLOW_PAID", raising=False)

    with pytest.raises(ConfigError, match="OPENROUTER_MODEL"):
        load_config(None)


def test_load_config_allows_explicit_paid_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("ADMINS_IDS", "123456789")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    monkeypatch.setenv("OPENROUTER_MODEL", "provider/model")
    monkeypatch.setenv("OPENROUTER_ALLOW_PAID", "true")

    settings = load_config(None)

    assert settings.openrouter_allow_paid is True


def _set_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("ADMINS_IDS", "123456789")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    monkeypatch.setenv("OPENROUTER_MODEL", "provider/model:free")


def test_empty_admins_ids_forbids_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("ADMINS_IDS", "")

    with pytest.raises(ConfigError, match="ADMINS_IDS не указан. Запуск бота запрещён."):
        load_config(None)


@pytest.mark.parametrize("admins_ids", ["abc", "123,-2", "123,,456", "123.0"])
def test_invalid_admins_ids_forbids_startup(
    monkeypatch: pytest.MonkeyPatch, admins_ids: str
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("ADMINS_IDS", admins_ids)

    with pytest.raises(ConfigError, match="ADMINS_IDS содержит некорректное значение"):
        load_config(None)


def test_admins_ids_with_spaces_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("ADMINS_IDS", "123456789, 987654321,555666777")

    settings = load_config(None)

    assert settings.admins_ids == frozenset({123456789, 987654321, 555666777})


def test_admins_ids_remove_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("ADMINS_IDS", "123456789,123456789, 987654321")

    settings = load_config(None)

    assert settings.admins_ids == frozenset({123456789, 987654321})
