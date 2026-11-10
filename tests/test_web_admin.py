from app.admin.keyboards import main_keyboard


def test_main_keyboard_contains_web_monitoring():
    callbacks = [button.callback_data for row in main_keyboard().inline_keyboard for button in row]
    assert "web:list" in callbacks

