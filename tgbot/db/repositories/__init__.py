from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.delivery import DeliveryRepository
from tgbot.db.repositories.invite import InviteRepository
from tgbot.db.repositories.material import MaterialRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.outbox import OutboxRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository

__all__ = [
    "UserRepository",
    "TrainingRepository",
    "MaterialRepository",
    "OrderRepository",
    "AccessRepository",
    "DeliveryRepository",
    "OutboxRepository",
    "InviteRepository",
]
