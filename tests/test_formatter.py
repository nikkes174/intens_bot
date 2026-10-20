from datetime import datetime, timezone
from app.services.formatter import format_notification


def test_basic():
    text = format_notification(
        "Title",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        1234,
        ["foo"],
        "hello",
        "https://t.me/x/1",
        "UTC",
    )
    assert "Title" in text
    assert "foo" in text
    assert "https://t.me/x/1" in text
    assert len(text) <= 4096


def test_no_audience():
    text = format_notification(
        "T", datetime(2026, 1, 1, tzinfo=timezone.utc), None, ["k"], "hi", None
    )
    assert "неизвестна" in text


def test_html_escape():
    text = format_notification(
        "<b>", datetime(2026, 1, 1, tzinfo=timezone.utc), 1, ["a&b"], "<script>", None
    )
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_truncate():
    long_text = "a" * 5000
    text = format_notification(
        "T", datetime(2026, 1, 1, tzinfo=timezone.utc), 1, ["k"], long_text, None
    )
    assert len(text) <= 4096
    assert text.endswith("…")

def test_special_chars():
    from datetime import datetime, timezone
    from app.services.formatter import format_notification
    text = format_notification("a<b>", datetime(2026,1,1,tzinfo=timezone.utc), 1, ["q&w"], "<script>&", "https://t.me/x/1?x=1&y=2")
    assert "&lt;script&gt;&amp;" in text
    assert len(text) <= 4096
