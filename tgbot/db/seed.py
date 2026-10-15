"""Seed data for initial trainings."""

import logging

from tgbot.db.db import get_session_factory
from tgbot.db.repositories.training import TrainingRepository

logger = logging.getLogger(__name__)

DEFAULT_TRAININGS = [
    {
        "title": "Тренировка №1",
        "description": "Программа тренировок для начинающих. Базовые упражнения для всего тела.",
        "price": 990,
        "sort_order": 1,
    },
    {
        "title": "Тренировка №2",
        "description": "Программа тренировок для среднего уровня. Развитие силы и выносливости.",
        "price": 990,
        "sort_order": 2,
    },
    {
        "title": "Тренировка №3",
        "description": "Интенсивная программа для продвинутых. Высокие нагрузки.",
        "price": 990,
        "sort_order": 3,
    },
    {
        "title": "Тренировка №4",
        "description": "Кардио-программа для похудения и тонуса.",
        "price": 990,
        "sort_order": 4,
    },
    {
        "title": "Тренировка №5",
        "description": "Комплексная программа: сила + кардио + гибкость.",
        "price": 990,
        "sort_order": 5,
    },
]


async def seed_trainings():
    """Create default trainings if none exist."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        existing = await training_repo.get_all()
        if existing:
            logger.info("Trainings already exist, skipping seed.")
            return

        for training_data in DEFAULT_TRAININGS:
            await training_repo.create(**training_data)

        logger.info("Seeded %d trainings.", len(DEFAULT_TRAININGS))
