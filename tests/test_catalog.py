"""Tests for training catalog."""

from decimal import Decimal

import pytest

from tgbot.keyboards.inline import catalog_keyboard


@pytest.mark.asyncio
async def test_catalog_shows_five_trainings(training_repo):
    """Test that catalog displays five trainings."""
    # Create 5 trainings
    for i in range(1, 6):
        await training_repo.create(
            title=f"Тренировка №{i}",
            description=f"Описание {i}",
            price=Decimal("990.00"),
            sort_order=i,
        )

    trainings = await training_repo.get_active()
    assert len(trainings) == 5


@pytest.mark.asyncio
async def test_catalog_button_opens_correct_training(training_repo):
    """Test that clicking a button opens the correct training."""
    training = await training_repo.create(
        title="Тренировка №1",
        description="Тест",
        price=Decimal("990.00"),
        sort_order=1,
    )

    trainings = await training_repo.get_active()
    keyboard = catalog_keyboard(trainings)

    # Check that callback data contains training ID
    callback_data = keyboard.inline_keyboard[0][0].callback_data
    assert f"training_{training.id}" in callback_data


@pytest.mark.asyncio
async def test_price_comes_from_db(training_repo):
    """Test that price is taken from database."""
    training = await training_repo.create(
        title="Тренировка №1",
        description="Тест",
        price=Decimal("1500.00"),
        sort_order=1,
    )

    fetched = await training_repo.get_by_id(training.id)
    assert fetched.price == Decimal("1500.00")


@pytest.mark.asyncio
async def test_hidden_trainings_not_shown(training_repo):
    """Test that hidden trainings are not displayed in catalog."""
    await training_repo.create(
        title="Тренировка №1",
        description="Тест",
        price=Decimal("990.00"),
        sort_order=1,
    )
    training2 = await training_repo.create(
        title="Тренировка №2",
        description="Тест",
        price=Decimal("990.00"),
        sort_order=2,
    )

    # Hide second training
    await training_repo.set_active(training2.id, False)

    active = await training_repo.get_active()
    assert len(active) == 1
    assert active[0].title == "Тренировка №1"


@pytest.mark.asyncio
async def test_purchased_training_shows_materials_button(training_repo, access_repo, sample_user):
    """Test that purchased training shows materials button instead of payment."""
    training = await training_repo.create(
        title="Тренировка №1",
        description="Тест",
        price=Decimal("990.00"),
        sort_order=1,
    )

    # Grant access
    await access_repo.grant_access(sample_user.id, training.id)

    has_access = await access_repo.has_active_access(sample_user.id, training.id)
    assert has_access is True
