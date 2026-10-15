from dataclasses import dataclass, field

from environs import Env


@dataclass
class DbConfig:
    url: str = "sqlite+aiosqlite:///bot.db"

    @staticmethod
    def from_env(env: Env) -> "DbConfig":
        url = env.str("DB", "sqlite+aiosqlite:///bot.db")
        return DbConfig(url=url)


@dataclass
class TgBot:
    token: str
    admin_ids: list[int] = field(default_factory=list)

    @staticmethod
    def from_env(env: Env) -> "TgBot":
        token = env.str("BOT_TOKEN")
        admin_ids = list(map(int, env.list("ADMINS", subcast=int)))
        return TgBot(token=token, admin_ids=admin_ids)


@dataclass
class YooKassaConfig:
    shop_id: str = ""
    secret_key: str = ""
    return_url: str = ""
    vat_code: int = 1
    webhook_path: str = "/webhooks/yookassa"

    @staticmethod
    def from_env(env: Env) -> "YooKassaConfig":
        return YooKassaConfig(
            shop_id=env.str("YOOKASSA_SHOP_ID", ""),
            secret_key=env.str("YOOKASSA_SECRET_KEY", ""),
            return_url=env.str("YOOKASSA_RETURN_URL", "https://t.me/"),
            vat_code=env.int("YOOKASSA_VAT_CODE", 1),
            webhook_path=env.str("YOOKASSA_WEBHOOK_PATH", "/webhooks/yookassa"),
        )


@dataclass
class WebhookConfig:
    host: str = "0.0.0.0"
    port: int = 8000

    @staticmethod
    def from_env(env: Env) -> "WebhookConfig":
        return WebhookConfig(
            host=env.str("WEBHOOK_HOST", "0.0.0.0"),
            port=env.int("WEBHOOK_PORT", 8000),
        )


@dataclass
class Miscellaneous:
    log_level: str = "INFO"

    @staticmethod
    def from_env(env: Env) -> "Miscellaneous":
        return Miscellaneous(log_level=env.str("LOG_LEVEL", "INFO"))


@dataclass
class Config:
    tg_bot: TgBot
    db: DbConfig
    yookassa: YooKassaConfig
    webhook: WebhookConfig
    misc: Miscellaneous


def load_config(path: str | None = None) -> Config:
    env = Env()
    if path:
        env.read_env(path)
    return Config(
        tg_bot=TgBot.from_env(env),
        db=DbConfig.from_env(env),
        yookassa=YooKassaConfig.from_env(env),
        webhook=WebhookConfig.from_env(env),
        misc=Miscellaneous.from_env(env),
    )
