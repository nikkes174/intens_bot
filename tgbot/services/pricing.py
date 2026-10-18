from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
DISCOUNT_RATE = Decimal("0.20")


def quantize_money(value: Decimal | str | int) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(value: Decimal | str | int) -> str:
    amount = quantize_money(value)
    rendered = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    if rendered.endswith(",00"):
        return rendered[:-3]
    return rendered.rstrip("0").rstrip(",")


def discounted_items(items: list[tuple[int, str, Decimal]]) -> tuple[Decimal, Decimal, list[dict]]:
    """Return discounted total, discount amount, and cent-exact receipt/order lines."""
    if not items:
        return Decimal("0.00"), Decimal("0.00"), []

    base_total = sum((quantize_money(price) for _, _, price in items), Decimal("0.00"))
    discounted_total = quantize_money(base_total * (Decimal("1.00") - DISCOUNT_RATE))
    discount_amount = quantize_money(base_total - discounted_total)
    discounted_prices = [
        quantize_money(price * (Decimal("1.00") - DISCOUNT_RATE)) for _, _, price in items
    ]
    # Allocate any rounding cent to the last item so receipt lines equal the charged total.
    discounted_prices[-1] += discounted_total - sum(discounted_prices, Decimal("0.00"))
    lines = [
        {
            "training_id": training_id,
            "description": title,
            "base_price": quantize_money(base_price),
            "item_price": item_price,
            "is_discounted": True,
        }
        for (training_id, title, base_price), item_price in zip(
            items, discounted_prices, strict=True
        )
    ]
    return discounted_total, discount_amount, lines
