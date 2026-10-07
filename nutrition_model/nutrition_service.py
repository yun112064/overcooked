import json
from pathlib import Path
from cooking_yield_service import (
    find_cooking_yield_by_ingredient,
    calculate_cooked_weight
)

from usda_cooked_nutrition_service import (
    find_usda_cooked_nutrition
)
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
    weight_g
):

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


    for nutrient, value in (
        ingredient[
            "nutrition_per_100g"
        ].items()
    ):

        if value is None:

            nutrition[
                nutrient
            ] = None

            missing_fields.append(
                nutrient
            )

        else:

            nutrition[
                nutrient
            ] = round(
                value * ratio,
                2
            )


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
    name,
    raw_weight_g,
    cooking_method
):

    # ==========================================
    # 1. 先算 TFDA 生食營養
    #    之後找不到完整熟食轉換時當 fallback
    # ==========================================

    raw_result = calculate_nutrition(
        name=name,
        weight_g=raw_weight_g
    )


    # ==========================================
    # 2. 查 USDA 熟食營養
    # ==========================================

    cooked_result = (
        find_usda_cooked_nutrition(
            ingredient_name=name,
            cooking_method=cooking_method
        )
    )


    # ==========================================
    # 3. 查完全相符的 USDA Cooking Yield
    # ==========================================

    yield_result = (
        find_cooking_yield_by_ingredient(
            ingredient_name=name,
            cooking_method=cooking_method
        )
    )


    # ==========================================
    # 情況 A：
    # 熟食營養 + Cooking Yield 都有
    # → 可以真正估算熟後營養
    # ==========================================

    if (
        cooked_result.get("success")
        and yield_result.get("success")
    ):

        cooking_yield_pct = (
            yield_result[
                "cooking_yield_pct"
            ]
        )

        cooked_weight_g = (
            calculate_cooked_weight(
                raw_weight_g=raw_weight_g,
                cooking_yield_pct=cooking_yield_pct
            )
        )

        nutrition_per_100g = (
            cooked_result[
                "nutrition_per_100g"
            ]
        )

        ratio = cooked_weight_g / 100

        cooked_nutrition = {}

        for nutrient, value in (
            nutrition_per_100g.items()
        ):

            if value is None:
                cooked_nutrition[
                    nutrient
                ] = None

            else:
                cooked_nutrition[
                    nutrient
                ] = round(
                    value * ratio,
                    2
                )


        return {
            "success": True,

            "ingredient": name,

            "raw_weight_g":
                raw_weight_g,

            "cooking_method":
                cooking_method,

            "cooking_yield_pct":
                cooking_yield_pct,

            "cooked_weight_g":
                cooked_weight_g,

            "nutrition_basis":
                "cooked_usda",

            "nutrition":
                cooked_nutrition,

            "cooking_adjustment_complete":
                True,

            "nutrition_source":
                cooked_result.get(
                    "source"
                ),

            "yield_source":
                yield_result.get(
                    "source"
                ),

            "missing_reason":
                None
        }


    # ==========================================
    # 情況 B：
    # 無法完成熟食轉換
    # → fallback 到 TFDA 生食營養
    # ==========================================

    if raw_result.get("success"):

        missing_reasons = []

        if not cooked_result.get(
            "success"
        ):
            missing_reasons.append(
                "找不到對應 USDA 熟食營養"
            )

        if not yield_result.get(
            "success"
        ):
            missing_reasons.append(
                "找不到與食材型態完全相符的 Cooking Yield"
            )


        return {
            "success": True,

            "ingredient": name,

            "raw_weight_g":
                raw_weight_g,

            "cooking_method":
                cooking_method,

            "cooking_yield_pct":
                None,

            "cooked_weight_g":
                None,

            # 重要：
            # 這不是熟食營養
            "nutrition_basis":
                "raw_tfda_estimate",

            "nutrition":
                raw_result[
                    "nutrition"
                ],

            "cooking_adjustment_complete":
                False,

            "nutrition_source":
                raw_result.get(
                    "source"
                ),

            "missing_reason":
                "；".join(
                    missing_reasons
                ),

            # 熟食資料雖然可能有，
            # 但缺 Yield 時不能拿來算整份重量
            "usda_cooked_reference":
                cooked_result
                if cooked_result.get(
                    "success"
                )
                else None
        }


    # ==========================================
    # 情況 C：
    # 熟食轉換不完整
    # TFDA 生食也找不到
    # ==========================================

    return {
        "success": False,

        "ingredient": name,

        "raw_weight_g":
            raw_weight_g,

        "cooking_method":
            cooking_method,

        "nutrition":
            None,

        "nutrition_basis":
            None,

        "cooking_adjustment_complete":
            False,

        "error":
            "nutrition_data_not_available",

        "missing_reason":
            "無法取得完整熟食轉換資料，且 TFDA 生食營養亦不存在"
    }

def calculate_recipe_nutrition(
    recipe_name,
    recipe_ingredients
):

    nutrient_keys = [
        "energy_kcal",
        "carbohydrate_g",
        "protein_g",
        "fat_g",
        "sodium_mg",
        "dietary_fiber_g"
    ]


    # =========================
    # 已知數值加總
    # =========================

    partial_totals = {
        key: 0.0
        for key in nutrient_keys
    }


    # 某營養只要其中一個食材缺值
    # 最後整道食譜該營養就不能宣稱完整
    missing_nutrients = {
        key: []
        for key in nutrient_keys
    }


    ingredient_results = []

    failed_ingredients = []

    cooking_incomplete = []


    # =========================
    # 逐一計算每個食材
    # =========================

    for item in recipe_ingredients:

        name = item.get("name")
        weight_g = item.get("weight_g")
        cooking_method = item.get(
            "cooking_method"
        )


        # ---------------------
        # 基本欄位檢查
        # ---------------------

        if (
            name is None
            or weight_g is None
        ):

            failed_ingredients.append(
                {
                    "ingredient": name,
                    "error":
                        "missing_name_or_weight"
                }
            )

            continue


        # ---------------------
        # 有料理方式
        # → 使用完整 cooking flow
        # ---------------------

        if cooking_method:

            result = (
                calculate_ingredient_nutrition_with_cooking(
                    name=name,
                    raw_weight_g=weight_g,
                    cooking_method=cooking_method
                )
            )


        # ---------------------
        # 沒料理方式
        # → 直接使用 TFDA 生食
        # ---------------------

        else:

            raw_result = calculate_nutrition(
                name=name,
                weight_g=weight_g
            )

            if raw_result.get("success"):

                result = {
                    "success": True,
                    "ingredient": name,
                    "raw_weight_g": weight_g,
                    "cooking_method": None,
                    "nutrition_basis":
                        "raw_tfda",
                    "nutrition":
                        raw_result["nutrition"],
                    "cooking_adjustment_complete":
                        False,
                    "nutrition_source":
                        raw_result.get(
                            "source"
                        ),
                    "missing_reason":
                        "食譜未提供料理方式"
                }

            else:

                result = raw_result


        ingredient_results.append(
            result
        )


        # =====================
        # 食材完全失敗
        # =====================

        if not result.get(
            "success"
        ):

            failed_ingredients.append(
                {
                    "ingredient":
                        name,

                    "error":
                        result.get(
                            "error"
                        )
                }
            )

            continue


        # =====================
        # Cooking 是否完整
        # =====================

        if not result.get(
            "cooking_adjustment_complete",
            False
        ):

            cooking_incomplete.append(
                {
                    "ingredient":
                        name,

                    "reason":
                        result.get(
                            "missing_reason"
                        )
                }
            )


        nutrition = result.get(
            "nutrition",
            {}
        )


        # =====================
        # 六項營養加總
        # =====================

        for key in nutrient_keys:

            value = nutrition.get(
                key
            )

            if value is None:

                missing_nutrients[
                    key
                ].append(
                    name
                )

            else:

                partial_totals[
                    key
                ] += value


    # =========================
    # 四捨五入
    # =========================

    for key in nutrient_keys:

        partial_totals[
            key
        ] = round(
            partial_totals[key],
            2
        )


    # =========================
    # 正式 recipe total
    #
    # 有任何食材完全失敗，或該營養項有缺值
    # → 該營養不能當作完整值
    # =========================

    total_nutrition = {}


    for key in nutrient_keys:

        if failed_ingredients or missing_nutrients[key]:

            total_nutrition[
                key
            ] = None

        else:

            total_nutrition[
                key
            ] = partial_totals[
                key
            ]


    # =========================
    # 整理真的有缺值的營養
    # =========================

    missing_nutrients = {
        key: ingredients
        for key, ingredients
        in missing_nutrients.items()
        if ingredients
    }


    nutrition_complete = (
        len(failed_ingredients) == 0
        and len(
            missing_nutrients
        ) == 0
    )


    cooking_adjustment_complete = (
        len(cooking_incomplete)
        == 0
    )


    return {
        "success":
            len(
                failed_ingredients
            ) == 0,

        "recipe_name":
            recipe_name,

        "ingredients":
            ingredient_results,

        "total_nutrition":
            total_nutrition,

        # 即使缺值，
        # 仍保留已知食材加總供除錯/參考
        "partial_nutrition_totals":
            partial_totals,

        "nutrition_complete":
            nutrition_complete,

        "cooking_adjustment_complete":
            cooking_adjustment_complete,

        "missing_nutrients":
            missing_nutrients,

        "cooking_incomplete":
            cooking_incomplete,

        "failed_ingredients":
            failed_ingredients
    }

def find_cooked_ingredient(
    name,
    cooking_method
):

    # 先取得標準名稱
    raw_ingredient = find_ingredient(name)

    if raw_ingredient is None:
        return None

    standard_name = raw_ingredient[
        "standard_name"
    ]

    # 找同一食材的熟食資料
    for ingredient in ingredients:

        if (
            ingredient["standard_name"]
            == standard_name
            and ingredient.get("state")
            == "cooked"
            and ingredient.get("cooking_method")
            == cooking_method
        ):
            return ingredient

    return None

def calculate_cooked_nutrition(
    name,
    raw_weight_g,
    cooking_method
):

    # =====================
    # 1. 找 USDA Cooking Yield
    # =====================

    yield_result = (
        find_cooking_yield_by_ingredient(
            ingredient_name=name,
            cooking_method=cooking_method
        )
    )

    if not yield_result["success"]:

        return {
            "success": False,
            "nutrition_complete": False,
            "ingredient": name,
            "error": "cooking_yield_missing",
            "missing_reason":
                "找不到對應的 USDA Cooking Yield"
        }


    cooking_yield_pct = (
        yield_result["cooking_yield_pct"]
    )


    # =====================
    # 2. 生重 → 熟重
    # =====================

    cooked_weight_g = (
        calculate_cooked_weight(
            raw_weight_g=raw_weight_g,
            cooking_yield_pct=cooking_yield_pct
        )
    )


    # =====================
    # 3. 找 TFDA 熟食資料
    # =====================

    cooked_ingredient = (
        find_cooked_ingredient(
            name=name,
            cooking_method=cooking_method
        )
    )


    # =====================
    # 4. 沒有熟食營養資料
    # =====================

    if cooked_ingredient is None:

        return {
            "success": True,

            "nutrition_complete": False,

            "ingredient": name,

            "raw_weight_g":
                raw_weight_g,

            "cooking_method":
                cooking_method,

            "cooking_yield_pct":
                cooking_yield_pct,

            "cooked_weight_g":
                cooked_weight_g,

            "nutrition": None,

            "missing_fields": [
                "cooked_nutrition"
            ],

            "missing_reason":
                "有 USDA Cooking Yield，但查無對應 TFDA 熟食營養資料",

            "yield_source":
                yield_result["source"]
        }


    # =====================
    # 5. 有熟食營養資料
    # =====================

    nutrition_per_100g = (
        cooked_ingredient[
            "nutrition_per_100g"
        ]
    )

    ratio = cooked_weight_g / 100


    nutrition = {
        nutrient: round(
            value * ratio,
            2
        )
        if value is not None
        else None

        for nutrient, value
        in nutrition_per_100g.items()
    }


    return {
        "success": True,

        "nutrition_complete": True,

        "ingredient":
            cooked_ingredient[
                "standard_name"
            ],

        "raw_weight_g":
            raw_weight_g,

        "cooking_method":
            cooking_method,

        "cooking_yield_pct":
            cooking_yield_pct,

        "cooked_weight_g":
            cooked_weight_g,

        "nutrition":
            nutrition,

        "nutrition_source":
            cooked_ingredient["source"],

        "yield_source":
            yield_result["source"]
    }
