from app.admin.keyboards import main_keyboard, web_watch_type_keyboard
from app.admin.web_monitor import _parse_watch_input


def test_main_keyboard_contains_web_monitoring():
    callbacks = [button.callback_data for row in main_keyboard().inline_keyboard for button in row]
    assert "web:list" in callbacks


def test_web_watch_type_keyboard_offers_all_target_types():
    callbacks = [
        button.callback_data
        for row in web_watch_type_keyboard().inline_keyboard
        for button in row
    ]
    assert callbacks[:3] == [
        "web:add_type:person",
        "web:add_type:organization",
        "web:add_type:keyword",
    ]


def test_watch_input_keeps_person_clarification_and_accepts_phrases():
    assert _parse_watch_input("Иван Иванов\nУточнение: Москва", "person") == (
        "иван иванов",
        "Москва",
    )
    assert _parse_watch_input("ООО Ромашка", "organization") == (
        "ооо ромашка",
        None,
    )
