"""Idempotently seed the four current products without deleting legacy records."""

import logging
from decimal import Decimal

from tgbot.db.db import get_session_factory
from tgbot.db.repositories.training import TrainingRepository
from tgbot.handlers.handlers_texts import PRODUCTS

logger = logging.getLogger(__name__)


async def seed_products() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        existing_products = await training_repo.get_products(active_only=False)
        existing_codes = {product.product_code for product in existing_products}

        # Keep old rows and their purchases, but retire their old catalog cards.
        all_trainings = await training_repo.get_all()
        for training in all_trainings:
            if training.product_code is None and training.is_active:
                await training_repo.set_active(training.id, False)

        products_by_code = {product.product_code: product for product in existing_products}
        for code, product_data in PRODUCTS.items():
            if code not in existing_codes:
                products_by_code[code] = await training_repo.create(
                    product_code=code,
                    title=product_data["title"],
                    description=product_data["description"],
                    price=Decimal(product_data["price"]),
                    sort_order=product_data["sort_order"],
                    is_bundle=product_data.get("is_bundle", False),
                )

        for bundle_code, product_data in PRODUCTS.items():
            if not product_data.get("is_bundle"):
                continue
            bundle = products_by_code[bundle_code]
            for component_code in product_data["components"]:
                component = products_by_code[component_code]
                await training_repo.add_bundle_component(bundle.id, component.id)

    logger.info("Product catalog initialized with %d products.", len(PRODUCTS))


# Kept as a compatibility alias for existing deployment scripts.
seed_trainings = seed_products
