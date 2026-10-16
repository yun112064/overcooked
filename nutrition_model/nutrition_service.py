import json
from pathlib import Path
from llm_cooking_estimator import finite_number, NUTRIENTS
from cooked_nutrition_service import calculate_cooking, calculate_recipe, find_cooked
MAPPING_PATH = (
    Path(__file__).parent
    / "data"
    / "tfda_food_mapping.json"
)

with open(
    MAPPING_PATH,
    "r",
    encoding="utf-8"
) as f:
    tfda_food_mapping = json.load(f)

DATA_PATH = Path(__file__).parent / "data" / "ingredient_master.json"


def load_ingredients():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


ingredients = load_ingredients()


def find_ingredient(name):

    # =========================
    # 1. 優先使用人工確認 mapping
    # =========================

    mapping = tfda_food_mapping.get(name)

    if mapping is not None:

        target_id = mapping[
            "ingredient_id"
        ]

        for ingredient in ingredients:

            if (
                ingredient[
                    "ingredient_id"
                ]
                == target_id
            ):
                return ingredient


    # =========================
    # 2. 正式名稱完全相同
    # =========================

    standard_matches = [
        ingredient
        for ingredient in ingredients
        if ingredient[
            "standard_name"
        ] == name
    ]

    if len(standard_matches) == 1:
        return standard_matches[0]


    # =========================
    # 3. alias
    # =========================

    alias_matches = [
        ingredient
        for ingredient in ingredients
        if name in ingredient.get(
            "aliases",
            []
        )
    ]

    if len(alias_matches) == 1:
        return alias_matches[0]


    # 多筆 alias：泛用俗名優先使用 TFDA 平均值。
    # 若沒有唯一平均值，保持查詢失敗，避免任意選取品種、冷凍或加工資料。
    average_matches = [
        ingredient
        for ingredient in alias_matches
        if "平均值" in ingredient.get(
            "standard_name",
            ""
        )
    ]

    if len(average_matches) == 1:
        return average_matches[0]


    # 多筆且無法唯一判定，不能亂選
    return None


def calculate_nutrition(
    name,
    weight_g,
    *, round_result=True
):

    if not isinstance(name, str) or not name.strip() or not finite_number(weight_g, positive=True):
        return {"success": False, "error": "invalid_nutrition_input"}

    ingredient = find_ingredient(
        name
    )

    if ingredient is None:

        return {
            "success": False,
            "error": "ingredient_not_found"
        }


    ratio = weight_g / 100

    nutrition = {}

    missing_fields = []


    for nutrient in NUTRIENTS:
        value = ingredient['nutrition_per_100g'].get(nutrient)

        if not finite_number(value):

            nutrition[
                nutrient
            ] = None

            missing_fields.append(
                nutrient
            )

        else:

            nutrition[nutrient] = round(value * ratio, 2) if round_result else value * ratio


    return {
        "success": True,

        "ingredient_id":
            ingredient[
                "ingredient_id"
            ],

        "standard_name":
            ingredient[
                "standard_name"
            ],

        "input_name":
            name,

        "state":
            ingredient.get(
                "state"
            ),

        "weight_g":
            weight_g,

        "nutrition":
            nutrition,

        "nutrition_complete":
            len(
                missing_fields
            ) == 0,

        "missing_fields":
            missing_fields,

        "source":
            ingredient[
                "source"
            ]
    }

def calculate_ingredient_nutrition_with_cooking(
    name, raw_weight_g, cooking_method, *, cooked_weight_g=None,
    weight_basis="raw", cooking_context=None, estimator=None
):
    return calculate_cooking(
        name, raw_weight_g, cooking_method, ingredients, find_ingredient,
        cooked_weight_g=cooked_weight_g, weight_basis=weight_basis,
        cooking_context=cooking_context, estimator=estimator
    )


def calculate_cooked_nutrition(name, raw_weight_g, cooking_method, **kwargs):
    """Compatibility entry point; shares the TFDA-first cooking pipeline."""
    return calculate_ingredient_nutrition_with_cooking(name, raw_weight_g, cooking_method, **kwargs)


def find_cooked_ingredient(name, cooking_method):
    return find_cooked(name, cooking_method, ingredients, find_ingredient)


def calculate_recipe_nutrition(recipe_name, recipe_ingredients, *, servings=1,
                               estimator=None, cooked_weight_g=None):
    return calculate_recipe(
        recipe_name, recipe_ingredients, calculate_ingredient_nutrition_with_cooking,
        calculate_nutrition, find_ingredient, servings=servings, estimator=estimator,
        cooked_weight_g=cooked_weight_g
    )
