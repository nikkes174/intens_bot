import importlib.util
import sys
import types
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


try:
    import environs  # noqa: F401
except ModuleNotFoundError:
    environs_stub = types.ModuleType("environs")
    environs_stub.Env = object
    sys.modules["environs"] = environs_stub


def _load_statistics_module():
    path = Path(__file__).parents[1] / "tgbot" / "handlers" / "statistics.py"
    spec = importlib.util.spec_from_file_location("statistics_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


statistics = _load_statistics_module()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("source:today", ("today", None)),
        ("source:all", ("all", None)),
        ("source:7d", ("7d", None)),
        ("source:30d", ("30d", None)),
        ("source:promo_*", ("promo_*", None)),
        ("source:today 7d", ("today", "7d")),
    ],
)
def test_source_namespace_does_not_reserve_period_names(args, expected):
    assert statistics.parse_arguments(args) == expected


def test_report_counts_delivered_payment_links_not_payment_creation():
    metrics = {
        "users": 1,
        "visits": 1,
        "payments": 1,
        "revenue": Decimal("100"),
        "conversion": 100.0,
        "events": {
            "payment_created": 99,
            "payment_link_sent": 3,
        },
    }

    overall = statistics.render_overall(metrics, [], None)
    campaign = statistics.render_campaign(metrics, "promo", None)

    assert "Получили ссылку на оплату: 3" in overall
    assert "Получили ссылку на оплату: 3" in campaign
    assert "Перешли к оплате: 99" not in overall
    assert "Перешли к оплате: 99" not in campaign


class _Message:
    def __init__(self, user_id, chat_type="private"):
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(type=chat_type)
        self.parts = []

    async def answer(self, text, parse_mode=None):
        self.parts.append((text, parse_mode))


@pytest.mark.asyncio
async def test_long_reports_are_split_below_telegram_limit():
    message = _Message(123)
    original = "x" * 10000

    await statistics.send_long_text(message, original)

    assert message.parts
    assert all(len(text) <= 3900 for text, _ in message.parts)
    assert all(parse_mode is None for _, parse_mode in message.parts)
    assert "".join(text for text, _ in message.parts) == original


@pytest.mark.asyncio
async def test_statistics_is_restricted_to_admin():
    message = _Message(124)
    config = SimpleNamespace(tg_bot=SimpleNamespace(admin_ids=[123]))
    await statistics.statistics_command(
        message,
        SimpleNamespace(args=None),
        config,
    )

    assert message.parts == []
