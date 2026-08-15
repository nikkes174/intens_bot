from aiogram.utils.keyboard import InlineKeyboardBuilder


def access_kb():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅️‍ПОДТВЕРЖДАЮ✅",
        callback_data="access_ok"
    )
    builder.adjust(1, 1)
    return builder.as_markup()

def first_start_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="️‍🟢Получить доступ🟢",
        callback_data="paying_for_subscriptions"
    )
    builder.adjust(1, 1)
    return builder.as_markup()

def to_back():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Назад 🔙️",
        callback_data="back_to"
    )
    builder.adjust(1, 1)
    return builder.as_markup()


def to_access():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔒 Войти в закрытый канал",
        url="https://t.me/+-E8hgZJHuaQ1NzVi"
    )
    builder.adjust(1, 1)
    return builder.as_markup()
