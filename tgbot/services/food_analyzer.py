from __future__ import annotations

import json
import re
from html import escape

from pydantic import ValidationError

from tgbot.schemas.food import FoodAnalysis, Ingredient, Nutrition
from tgbot.services.openrouter import OpenRouterClient, OpenRouterResponseError

FOOD_ANALYSIS_PROMPT = """Ты — помощник по распознаванию продуктов питания.

Пользователь присылает фотографию еды.

Твоя задача:
1. Определить видимые блюда.
2. Определить основные продукты.
3. Предположить размеры порций.
4. Оценить приблизительный вес.
5. Оценить калорийность.
6. Оценить белки, жиры и углеводы.
7. Кратко объяснить состав блюда.

Правила:
- Не придумывай продукты, которых не видно.
- Не утверждай, что точно знаешь вес порций.
- Не выдавай оценочные данные за измеренные.
- Не придумывай скрытые ингредиенты.
- Если масло, соус или способ приготовления неизвестны, сообщи об этом.
- Если изображение невозможно распознать, верни соответствующий статус.
- Если на фотографии нет еды, не придумывай блюдо.
- Не ставь диагнозы и не давай медицинских назначений.
- Не используй осуждающие формулировки.
- Отвечай на русском языке.
- Возвращай только JSON без markdown-обёртки.

Формат ответа:
{
  "recognized": true,
  "dish_name": "Курица с рисом и овощами",
  "ingredients": [
    {"name": "Куриное филе", "weight_g": 150}
  ],
  "nutrition": {
    "calories": 530,
    "proteins": 52,
    "fats": 9,
    "carbs": 56
  },
  "comment": "Блюдо содержит белок, углеводы и овощи.",
  "warning": "Количество масла и соусов неизвестно."
}

Все числовые значения являются оценкой модели. Модель не может точно измерить калории,
вес и БЖУ по фотографии. Если еды нет, верни recognized=false, пустой список ingredients,
nutrition=null, а dish_name — null."""

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


def _extract_json(raw_response: str) -> str:
    response = raw_response.strip()
    block = _JSON_BLOCK.search(response)
    if block:
        response = block.group(1).strip()
    if not response.startswith("{"):
        start = response.find("{")
        end = response.rfind("}")
        if start < 0 or end <= start:
            raise OpenRouterResponseError
        response = response[start : end + 1]
    return response


def parse_food_analysis(raw_response: str) -> FoodAnalysis:
    try:
        payload = json.loads(_extract_json(raw_response))
        return FoodAnalysis.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, OpenRouterResponseError) as error:
        raise OpenRouterResponseError from error


async def analyze_food(image_bytes: bytes, client: OpenRouterClient) -> FoodAnalysis:
    raw_response = await client.analyze_photo(image_bytes, FOOD_ANALYSIS_PROMPT)
    return parse_food_analysis(raw_response)


def _number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}".replace(".", ",")


def _ingredient_icon(name: str) -> str:
    normalized = name.lower()
    if any(word in normalized for word in ("кур", "мяс", "гов", "свин", "индей")):
        return "🥩"
    if any(word in normalized for word in ("рыб", "лосос", "тунец", "морепр")):
        return "🐟"
    if any(word in normalized for word in ("овощ", "салат", "огур", "помид", "брокк")):
        return "🥦"
    if any(word in normalized for word in ("рис", "паст", "макарон", "картоф")):
        return "🍚"
    if any(word in normalized for word in ("хлеб", "булк", "тост")):
        return "🍞"
    if any(word in normalized for word in ("фрукт", "яблок", "банан", "ягод")):
        return "🍎"
    if any(word in normalized for word in ("сыр", "молок", "йогур")):
        return "🧀"
    return "🍽"


def format_food_result(analysis: FoodAnalysis) -> str:
    dish_name = escape(analysis.dish_name or "Блюдо")
    ingredients = "\n".join(
        (
            f"{_ingredient_icon(item.name)} {escape(item.name)} — ~{_number(item.weight_g)} г"
            if item.weight_g is not None
            else f"{_ingredient_icon(item.name)} {escape(item.name)} — вес не удалось оценить"
        )
        for item in analysis.ingredients
    )
    nutrition = analysis.nutrition
    if nutrition is None:
        raise ValueError("Для распознанного блюда отсутствует пищевая ценность")

    warning = analysis.warning or "Количество масла и соусов неизвестно."
    return (
        f"🍽 <b>{dish_name}</b>\n\n"
        f"🔎 <b>Что я вижу:</b>\n\n{ingredients or '• Состав сложно определить по фотографии.'}\n\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Примерная пищевая ценность</b>\n\n"
        f"🔥 Калорийность: ~{_number(nutrition.calories)} ккал\n\n"
        f"🥩 Белки: ~{_number(nutrition.proteins)} г\n"
        f"🥑 Жиры: ~{_number(nutrition.fats)} г\n"
        f"🍞 Углеводы: ~{_number(nutrition.carbs)} г\n\n"
        "━━━━━━━━━━━━━━━━\n\n"
        f"💡 <b>О блюде</b>\n\n{escape(analysis.comment)}\n\n"
        "⚠️ "
        f"{escape(warning)}\n"
        "Фактическая калорийность может отличаться от оценки.\n\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "📸 Хочешь проверить другое блюдо?\n"
        "Просто отправь новую фотографию."
    )


def format_no_food_result() -> str:
    return (
        "🤔 Кажется, на фотографии нет еды.\n\n"
        "Попробуй отправить снимок блюда,\nпродукта или напитка."
    )


def rescale_analysis(analysis: FoodAnalysis, target_weight: float) -> FoodAnalysis | None:
    if not analysis.ingredients or analysis.nutrition is None:
        return None
    if any(item.weight_g is None or item.weight_g <= 0 for item in analysis.ingredients):
        return None

    original_weight = sum(item.weight_g for item in analysis.ingredients)
    if original_weight <= 0:
        return None
    multiplier = target_weight / original_weight
    ingredients = [
        Ingredient(name=item.name, weight_g=item.weight_g * multiplier)
        for item in analysis.ingredients
    ]
    nutrition = Nutrition(
        calories=analysis.nutrition.calories * multiplier,
        proteins=analysis.nutrition.proteins * multiplier,
        fats=analysis.nutrition.fats * multiplier,
        carbs=analysis.nutrition.carbs * multiplier,
    )
    warning = analysis.warning or "Количество масла и соусов неизвестно."
    warning += " Пересчёт выполнен пропорционально новому общему весу блюда."
    return FoodAnalysis(
        recognized=True,
        dish_name=analysis.dish_name,
        ingredients=ingredients,
        nutrition=nutrition,
        comment=analysis.comment,
        warning=warning,
    )
