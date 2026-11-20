from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

SHOW_FOOD_BUTTON_TEXT = "📸 Покажу, что ем"
REFINE_WEIGHT_BUTTON_TEXT = "⚖️ Уточнить порцию"
ANALYZE_AGAIN_BUTTON_TEXT = "📸 Посмотреть другое блюдо"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=SHOW_FOOD_BUTTON_TEXT)]],
        resize_keyboard=True,
    )


def analysis_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=REFINE_WEIGHT_BUTTON_TEXT, callback_data="refine_weight")],
            [InlineKeyboardButton(text=ANALYZE_AGAIN_BUTTON_TEXT, callback_data="analyze_again")],
        ]
    )


def retry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ANALYZE_AGAIN_BUTTON_TEXT, callback_data="analyze_again")]
        ]
    )
