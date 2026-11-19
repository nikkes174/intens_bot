from pydantic import BaseModel, ConfigDict, Field, model_validator


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    weight_g: float | None = Field(default=None, ge=0, le=100_000)


class Nutrition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    calories: float = Field(ge=0, le=1_000_000)
    proteins: float = Field(ge=0, le=1_000_000)
    fats: float = Field(ge=0, le=1_000_000)
    carbs: float = Field(ge=0, le=1_000_000)


class FoodAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recognized: bool
    dish_name: str | None = Field(default=None, max_length=200)
    ingredients: list[Ingredient] = Field(default_factory=list, max_length=100)
    nutrition: Nutrition | None = None
    comment: str = Field(default="", max_length=1000)
    warning: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_consistency(self) -> "FoodAnalysis":
        if self.recognized and (not self.dish_name or self.nutrition is None):
            raise ValueError("Для распознанного блюда нужны dish_name и nutrition")
        if not self.recognized and self.nutrition is not None:
            raise ValueError("Для фотографии без еды nutrition должен быть null")
        return self
