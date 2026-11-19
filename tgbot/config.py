import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator


class ConfigError(ValueError):
    """Ошибка обязательной конфигурации приложения."""


class Settings(BaseModel):
    bot_token: str = Field(min_length=1)
    openrouter_api_key: str = Field(min_length=1)
    openrouter_model: str = Field(min_length=1)
    openrouter_allow_paid: bool = False
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout: float = Field(default=45.0, gt=0)
    log_level: str = "INFO"

    @field_validator("openrouter_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("OPENROUTER_BASE_URL должен начинаться с http:// или https://")
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def validate_model_pricing(self) -> "Settings":
        if not self.openrouter_allow_paid and not self.openrouter_model.endswith(":free"):
            raise ValueError(
                "OPENROUTER_MODEL должен заканчиваться на :free, "
                "либо установите OPENROUTER_ALLOW_PAID=true"
            )
        return self


def _parse_bool(value: str, variable_name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigError(
        f"{variable_name} должен иметь значение true или false, получено некорректное значение"
    )


def load_config(path: str | Path | None = ".env") -> Settings:
    if path is not None:
        load_dotenv(path)

    env_values = {
        "bot_token": os.getenv("BOT_TOKEN", "").strip(),
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", "").strip(),
        "openrouter_model": os.getenv("OPENROUTER_MODEL", "").strip(),
        "openrouter_allow_paid": _parse_bool(
            os.getenv("OPENROUTER_ALLOW_PAID", "false"), "OPENROUTER_ALLOW_PAID"
        ),
        "openrouter_base_url": os.getenv(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ).strip(),
        "openrouter_timeout": os.getenv("OPENROUTER_TIMEOUT", "45").strip(),
        "log_level": os.getenv("LOG_LEVEL", "INFO").strip(),
    }
    missing = [
        variable
        for variable, key in (
            ("BOT_TOKEN", "bot_token"),
            ("OPENROUTER_API_KEY", "openrouter_api_key"),
            ("OPENROUTER_MODEL", "openrouter_model"),
        )
        if not env_values[key]
    ]
    if missing:
        raise ConfigError("Не заполнены обязательные переменные окружения: " + ", ".join(missing))

    try:
        return Settings(**env_values)
    except ValueError as error:
        raise ConfigError(f"Некорректная конфигурация: {error}") from error
