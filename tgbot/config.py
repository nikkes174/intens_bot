import logging
from dataclasses import dataclass, field

from environs import Env

logger = logging.getLogger(__name__)

TRAINING_GROUP_ENV_VARS = {
    "glute": "GLUTE_ID",
    "vshape": "V_SHARE_ID",
    "core": "CORE_PROTOCOL_ID",
}
CORE_PRODUCT_CODES = frozenset(TRAINING_GROUP_ENV_VARS)
BUNDLE_PRODUCT_CODE = "bundle_all"


def _optional_chat_id(env: Env, env_name: str) -> int | None:
    value = env.str(env_name, "").strip()
    if not value:
        logger.warning("Environment variable %s is empty; its training group is disabled", env_name)
        return None
    try:
        chat_id = int(value)
    except ValueError:
        logger.error("Environment variable %s must contain a numeric Telegram chat_id", env_name)
        return None
    if chat_id >= 0 or not -(2**63) <= chat_id < 2**63:
        logger.error(
            "Environment variable %s must contain a negative signed 64-bit Telegram group ID",
            env_name,
        )
        return None
    return chat_id


@dataclass(frozen=True)
class TrainingGroups:
    glute_id: int | None = None
    v_share_id: int | None = None
    core_protocol_id: int | None = None

    @staticmethod
    def from_env(env: Env) -> "TrainingGroups":
        groups = TrainingGroups(
            glute_id=_optional_chat_id(env, "GLUTE_ID"),
            v_share_id=_optional_chat_id(env, "V_SHARE_ID"),
            core_protocol_id=_optional_chat_id(env, "CORE_PROTOCOL_ID"),
        )
        for chat_id, product_codes in groups.duplicate_chat_ids().items():
            logger.error(
                "Telegram chat_id %s is configured for multiple products %s; "
                "those groups are disabled",
                chat_id,
                ", ".join(product_codes),
            )
        return groups

    def configured_chat_ids(self) -> dict[str, int | None]:
        return {
            "glute": self.glute_id,
            "vshape": self.v_share_id,
            "core": self.core_protocol_id,
        }

    def duplicate_chat_ids(self) -> dict[int, tuple[str, ...]]:
        products_by_id: dict[int, list[str]] = {}
        for product_code, chat_id in self.configured_chat_ids().items():
            if chat_id is not None:
                products_by_id.setdefault(chat_id, []).append(product_code)
        return {
            chat_id: tuple(product_codes)
            for chat_id, product_codes in products_by_id.items()
            if len(product_codes) > 1
        }

    def get_training_chat_id(self, product_code: str) -> int | None:
        if product_code == BUNDLE_PRODUCT_CODE or product_code not in CORE_PRODUCT_CODES:
            return None
        chat_id = self.configured_chat_ids()[product_code]
        if chat_id is None or chat_id in self.duplicate_chat_ids():
            return None
        return chat_id

    def get_training_product_code(self, chat_id: int) -> str | None:
        matches = [
            product_code
            for product_code in CORE_PRODUCT_CODES
            if self.get_training_chat_id(product_code) == chat_id
        ]
        return matches[0] if len(matches) == 1 else None

    def is_configured_chat_id(self, chat_id: int) -> bool:
        return chat_id in self.configured_chat_ids().values()


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
    webhook_path: str = "/webhooks/yookassa"

    @staticmethod
    def from_env(env: Env) -> "YooKassaConfig":
        return YooKassaConfig(
            shop_id=env.str("YOOKASSA_SHOP_ID", ""),
            secret_key=env.str("YOOKASSA_SECRET_KEY", ""),
            return_url=env.str("YOOKASSA_RETURN_URL", "https://t.me/"),
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
    training_groups: TrainingGroups = field(default_factory=TrainingGroups)


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
        training_groups=TrainingGroups.from_env(env),
    )
