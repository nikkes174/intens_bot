import pytest

from tgbot.schemas.food import FoodAnalysis, Ingredient, Nutrition
from tgbot.services.food_analyzer import (
    format_food_result,
    format_no_food_result,
    parse_food_analysis,
    rescale_analysis,
)
from tgbot.services.openrouter import OpenRouterResponseError

RECOGNIZED_JSON = """
{
  "recognized": true,
  "dish_name": "Курица с рисом",
  "ingredients": [
    {"name": "Куриное филе", "weight_g": 150},
    {"name": "Рис", "weight_g": 180}
  ],
  "nutrition": {"calories": 530, "proteins": 52, "fats": 9, "carbs": 56},
  "comment": "Белок, углеводы и овощи.",
  "warning": "Масло неизвестно."
}
"""


def test_parse_food_analysis_accepts_json_code_block() -> None:
    result = parse_food_analysis(f"```json\n{RECOGNIZED_JSON}\n```")

    assert result.recognized is True
    assert result.ingredients[0].weight_g == 150
    assert result.nutrition is not None


def test_parse_food_analysis_rejects_invalid_json() -> None:
    with pytest.raises(OpenRouterResponseError):
        parse_food_analysis("not json")


def test_parse_food_analysis_supports_no_food_result() -> None:
    result = parse_food_analysis(
        '{"recognized": false, "dish_name": null, "ingredients": [], '
        '"nutrition": null, "comment": "На фото нет еды."}'
    )

    assert result.recognized is False
    assert format_no_food_result().startswith("🤔")


def test_format_food_result_contains_nutrition_and_warning() -> None:
    result = format_food_result(parse_food_analysis(RECOGNIZED_JSON))

    assert "Курица с рисом" in result
    assert "~530 ккал" in result
    assert "~52 г" in result
    assert "Масло неизвестно." in result


def test_rescale_analysis_uses_new_total_weight() -> None:
    analysis = FoodAnalysis(
        recognized=True,
        dish_name="Овсянка",
        ingredients=[Ingredient(name="Овсянка", weight_g=100)],
        nutrition=Nutrition(calories=300, proteins=10, fats=5, carbs=50),
        comment="Крупа.",
    )

    updated = rescale_analysis(analysis, 150)

    assert updated is not None
    assert updated.ingredients[0].weight_g == 150
    assert updated.nutrition is not None
    assert updated.nutrition.calories == 450


def test_rescale_analysis_does_not_guess_when_weights_are_missing() -> None:
    analysis = FoodAnalysis(
        recognized=True,
        dish_name="Неизвестное блюдо",
        ingredients=[],
        nutrition=Nutrition(calories=100, proteins=1, fats=1, carbs=10),
    )

    assert rescale_analysis(analysis, 450) is None


def test_format_food_result_marks_unknown_ingredient_weight() -> None:
    analysis = FoodAnalysis(
        recognized=True,
        dish_name="Салат",
        ingredients=[Ingredient(name="Соус", weight_g=None)],
        nutrition=Nutrition(calories=100, proteins=1, fats=5, carbs=10),
    )

    assert "вес не удалось оценить" in format_food_result(analysis)
