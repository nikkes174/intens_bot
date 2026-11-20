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

Стиль поля comment:
- Пиши живым, взрослым и естественным русским языком.
- Кратко опиши сочетание продуктов и общие впечатления от блюда.
- Используй одно-два полезных предложения без бессмысленной похвалы.
- Не обращайся к пользователю свысока и не давай непрошеных советов.

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
- Не пиши, что пользователь молодец, должен есть меньше или испортит фигуру.
- Не используй детские, приторные или романтические обращения.
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
  "comment": "Хорошее сочетание белка, углеводов и овощей. Такой обед выглядит сытным.",
  "warning": "Количество масла и соусов неизвестно."
}

Все числовые значения являются оценкой модели. Модель не может точно измерить калории,
вес и БЖУ по фотографии. Если еды нет, верни recognized=false, пустой список ingredients,
nutrition=null, а dish_name — null."""

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
TELEGRAM_MESSAGE_LIMIT = 4096
RESCALE_WARNING = "Оценка обновлена с учётом указанного веса порции."


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


def _html_text(value: str, max_length: int) -> str:
    """Escape text and truncate it without cutting an HTML entity."""
    raw_value = str(value or "")
    escaped_value = escape(raw_value)
    if len(escaped_value) <= max_length:
        return escaped_value

    suffix = "…"
    low = 0
    high = len(raw_value)
    best = suffix if len(suffix) <= max_length else ""
    while low <= high:
        middle = (low + high) // 2
        candidate = escape(raw_value[:middle]) + suffix
        if len(candidate) <= max_length:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


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


def _build_food_result(
    analysis: FoodAnalysis,
    *,
    dish_limit: int,
    ingredient_limit: int,
    comment_limit: int,
    warning_limit: int,
    additional_limit: int,
) -> str:
    dish_name = _html_text(analysis.dish_name or "Блюдо", dish_limit)
    ingredient_lines = []
    for item in analysis.ingredients[:12]:
        name = _html_text(item.name, ingredient_limit)
        if item.weight_g is not None:
            ingredient_lines.append(
                f"{_ingredient_icon(item.name)} {name} — ~{_number(item.weight_g)} г"
            )
        else:
            ingredient_lines.append(
                f"{_ingredient_icon(item.name)} {name} — примерный вес неизвестен"
            )

    ingredients = "\n".join(ingredient_lines)
    extra_count = max(0, len(analysis.ingredients) - 12)
    if extra_count:
        extra_names = ", ".join(item.name for item in analysis.ingredients[12:20])
        if extra_count > 8:
            extra_names += f" (+{extra_count - 8})"
        ingredients += f"\n➕ Дополнительно: {_html_text(extra_names, additional_limit)}"

    nutrition = analysis.nutrition
    if nutrition is None:
        raise ValueError("Для распознанного блюда отсутствует пищевая ценность")

    warning = _html_text(analysis.warning, warning_limit) if analysis.warning else ""
    warning_block = f"\n\nℹ️ {warning}" if warning else ""
    return (
        "Аня, готово! 🌿\n\n"
        f"🍽 <b>{dish_name}</b>\n\n"
        "Я определил следующий состав:\n\n"
        f"{ingredients or 'Состав сложно определить по фотографии.'}\n\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Пищевая ценность</b>\n\n"
        f"🔥 Калории: ~{_number(nutrition.calories)} ккал\n\n"
        f"🥩 Белки: ~{_number(nutrition.proteins)} г\n"
        f"🥑 Жиры: ~{_number(nutrition.fats)} г\n"
        f"🍞 Углеводы: ~{_number(nutrition.carbs)} г\n\n"
        "━━━━━━━━━━━━━━━━\n\n"
        f"💬 <b>Моё мнение</b>\n\n{_html_text(analysis.comment, comment_limit)}"
        f"{warning_block}\n\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "Если в меню появится что-то ещё,\n"
        "ты знаешь, кому отправить фото. 😉"
    )


def _telegram_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def format_food_result(analysis: FoodAnalysis) -> str:
    result = _build_food_result(
        analysis,
        dish_limit=160,
        ingredient_limit=80,
        comment_limit=600,
        warning_limit=500,
        additional_limit=300,
    )
    if _telegram_length(result) <= TELEGRAM_MESSAGE_LIMIT:
        return result

    result = _build_food_result(
        analysis,
        dish_limit=80,
        ingredient_limit=40,
        comment_limit=250,
        warning_limit=250,
        additional_limit=160,
    )
    if _telegram_length(result) <= TELEGRAM_MESSAGE_LIMIT:
        return result

    nutrition = analysis.nutrition
    if nutrition is None:
        raise ValueError("Для распознанного блюда отсутствует пищевая ценность")
    return (
        "Аня, готово! 🌿\n\n"
        f"🍽 <b>{_html_text(analysis.dish_name or 'Блюдо', 60)}</b>\n\n"
        "📊 <b>Пищевая ценность</b>\n\n"
        f"🔥 Калории: ~{_number(nutrition.calories)} ккал\n\n"
        f"🥩 Белки: ~{_number(nutrition.proteins)} г\n"
        f"🥑 Жиры: ~{_number(nutrition.fats)} г\n"
        f"🍞 Углеводы: ~{_number(nutrition.carbs)} г\n\n"
        "ℹ️ Ответ получился подробным, поэтому часть деталей я сократил."
    )


def format_no_food_result() -> str:
    return (
        "Аня, с этим снимком у меня не сложилось. 🤔\n\n"
        "Не получилось уверенно определить,\n"
        "что на фотографии.\n\n"
        "Попробуем ещё раз?\n"
        "Может, с другого ракурса."
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
    warning = analysis.warning or ""
    warning = re.sub(rf"(?:\s*{re.escape(RESCALE_WARNING)})+\s*$", "", warning).strip()
    warning = f"{warning} {RESCALE_WARNING}".strip()
    return FoodAnalysis(
        recognized=True,
        dish_name=analysis.dish_name,
        ingredients=ingredients,
        nutrition=nutrition,
        comment=analysis.comment,
        warning=warning,
    )
