"""Tests for the current product catalog and customer-facing keyboards."""

from decimal import Decimal

import pytest

from tgbot.db.seed import seed_products
from tgbot.handlers.handlers_texts import PRODUCTS
from tgbot.keyboards.inline import (
    catalog_keyboard,
    main_menu_keyboard,
    product_card_keyboard,
)


@pytest.mark.asyncio
async def test_catalog_seeds_three_protocols_and_bundle(training_repo):
    await seed_products()

    products = await training_repo.get_products()
    by_code = {item.product_code: item for item in products}
    assert set(by_code) == {"glute", "vshape", "core", "bundle_all"}
    assert by_code["glute"].price == Decimal("1490.00")
    assert by_code["vshape"].price == Decimal("1290.00")
    assert by_code["core"].price == Decimal("1090.00")
    assert by_code["bundle_all"].price == Decimal("3290.00")

    component_ids = await training_repo.get_component_ids(by_code["bundle_all"].id)
    assert set(component_ids) == {by_code["glute"].id, by_code["vshape"].id, by_code["core"].id}


@pytest.mark.asyncio
async def test_bundle_regular_price_and_saving(training_repo):
    await seed_products()
    products = {item.product_code: item for item in await training_repo.get_products()}
    regular_total = sum(
        (products[code].price for code in ("glute", "vshape", "core")),
        Decimal("0.00"),
    )
    assert regular_total == Decimal("3870.00")
    assert regular_total - products["bundle_all"].price == Decimal("580.00")


@pytest.mark.asyncio
async def test_catalog_keyboard_uses_stable_product_codes(training_repo):
    await seed_products()
    keyboard = catalog_keyboard(await training_repo.get_products())
    callbacks = [row[0].callback_data for row in keyboard.inline_keyboard]
    assert callbacks == [
        "product_glute",
        "product_vshape",
        "product_core",
        "product_bundle_all",
    ]


@pytest.mark.asyncio
async def test_reseeding_does_not_duplicate_products_or_components(training_repo):
    await seed_products()
    await seed_products()
    products = await training_repo.get_products(active_only=False)
    assert len(products) == 4
    bundle = await training_repo.get_by_code("bundle_all")
    assert len(await training_repo.get_bundle_components(bundle.id)) == 3


@pytest.mark.asyncio
async def test_card_has_purchase_button_and_stable_callback(training_repo):
    await seed_products()
    product = await training_repo.get_by_code("glute")
    keyboard = product_card_keyboard(product, is_purchased=False)
    assert keyboard.inline_keyboard[0][0].callback_data == "buy_glute"
    assert keyboard.inline_keyboard[0][0].text == "💳 Купить за 1 490 ₽"


@pytest.mark.asyncio
async def test_purchased_card_has_material_and_channel_buttons(training_repo):
    await seed_products()
    product = await training_repo.get_by_code("vshape")
    keyboard = product_card_keyboard(product, is_purchased=True)
    assert [row[0].text for row in keyboard.inline_keyboard[:2]] == [
        "📂 Получить материалы",
        "🎥 Перейти в канал",
    ]
    assert keyboard.inline_keyboard[0][0].callback_data == f"get_materials_{product.id}"


def test_menu_buttons_and_welcome_name_escaping():
    import html

    from tgbot.handlers.user import _welcome

    keyboard = main_menu_keyboard()
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels[:4] == [
        "🍑 Glute Protocol",
        "🔙 V-Shape",
        "🎯 Core Protocol",
        "💎 Все 3 со скидкой",
    ]
    assert labels[4:6] == ["📂 Мои покупки", "❓ Вопросы"]
    assert keyboard.inline_keyboard[4][1].callback_data == "faq"
    assert keyboard.inline_keyboard[5][0].url == "https://t.me/annkrasko"

    escaped = _welcome("Анна <script>")
    assert html.escape("Анна <script>") in escaped
    assert "<script>" not in escaped
    assert _welcome(None).startswith("Привет! 👋")


def test_default_card_copy_has_expected_prices_and_bundle_contents():
    assert PRODUCTS["glute"]["price"] == "1490.00"
    assert PRODUCTS["vshape"]["price"] == "1290.00"
    assert PRODUCTS["core"]["price"] == "1090.00"
    assert "Обычная стоимость: 3 870 ₽" in PRODUCTS["bundle_all"]["description"]
    assert "Экономия: 580 ₽" in PRODUCTS["bundle_all"]["description"]
