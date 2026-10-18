"""Eligibility and cent-exact pricing for the post-purchase offer."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from tgbot.services.pricing import discounted_items

OFFER_PRODUCT_CODES = ("glute", "vshape", "core")
MINIMUM_OWNERSHIP_AGE = timedelta(days=7)


@dataclass(frozen=True)
class DiscountEligibility:
    available: bool
    reason: str | None
    missing_products: tuple
    total: Decimal
    discount_amount: Decimal
    lines: tuple[dict, ...]


async def check_discount_eligibility(
    user_id: int,
    *,
    access_repo,
    order_repo,
    training_repo,
    now: datetime | None = None,
) -> DiscountEligibility:
    """Return the current offer state using active rights and confirmed purchases."""
    current = now or datetime.utcnow()
    owned_product_ids = {
        access.training_id for access in await access_repo.get_user_accesses(user_id)
    }
    purchased_product_ids = set(owned_product_ids)

    qualifying_purchase_times = []
    for order in await order_repo.get_user_orders(user_id):
        if order.status != "succeeded":
            continue
        order_items = await order_repo.get_order_items(order.id)
        order_product_ids = [item.training_id for item in order_items] or [order.training_id]
        for training_id in order_product_ids:
            product = await training_repo.get_by_id(training_id)
            if not product:
                continue
            purchased_product_ids.add(product.id)
            if product.is_bundle:
                purchased_product_ids.update(await training_repo.get_component_ids(product.id))
            elif product.product_code in OFFER_PRODUCT_CODES:
                paid_at = order.paid_at or order.created_at
                if paid_at:
                    qualifying_purchase_times.append(paid_at)
    missing_products = []
    for code in OFFER_PRODUCT_CODES:
        product = await training_repo.get_by_code(code)
        if product and product.is_active and product.id not in purchased_product_ids:
            missing_products.append(product)

    priced_items = [
        (product.id, product.title, Decimal(product.price)) for product in missing_products
    ]
    total, discount_amount, lines = discounted_items(priced_items)

    if not qualifying_purchase_times:
        return DiscountEligibility(
            False,
            "no_individual_purchase",
            tuple(missing_products),
            total,
            discount_amount,
            tuple(lines),
        )

    if not any(current >= paid_at + MINIMUM_OWNERSHIP_AGE for paid_at in qualifying_purchase_times):
        return DiscountEligibility(
            False,
            "purchase_too_recent",
            tuple(missing_products),
            total,
            discount_amount,
            tuple(lines),
        )

    if not missing_products:
        return DiscountEligibility(
            False, "all_programs_owned", (), Decimal("0.00"), Decimal("0.00"), ()
        )

    return DiscountEligibility(
        True, None, tuple(missing_products), total, discount_amount, tuple(lines)
    )
