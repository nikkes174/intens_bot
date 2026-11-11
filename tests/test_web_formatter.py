from types import SimpleNamespace

from app.services.web_monitor.formatter import format_web_notification


def test_web_notification_uses_short_full_text_fragment_and_escapes_html():
    article = SimpleNamespace(
        title="Заголовок <важно>",
        source_name="РБК & партнёры",
        description="Описание",
        full_text="До этого была вводная часть. Иван Иванов сообщил о решении. После этого текст продолжается.",
        published_at=None,
    )
    text = format_web_notification(
        article,
        ["Иван Иванов"],
        matched_texts=["иван иванов"],
    )
    assert "💬 <b>Фрагмент:</b>" in text
    assert "Иван Иванов сообщил" in text
    assert "&lt;важно&gt;" in text
    assert "РБК &amp; партнёры" in text
    assert "Описание" not in text
