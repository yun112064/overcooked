import json
from pathlib import Path


BASE_DIR = Path(__file__).parent


USDA_NUTRITION_PATH = (
    BASE_DIR
    / "data"
    / "usda_cooked_nutrition.json"
)


MAPPING_PATH = (
    BASE_DIR
    / "data"
    / "usda_food_mapping.json"
)


# =========================
# 中文料理方式
# → import_usda_nutrition.py
# 使用的標準料理方式
# =========================

METHOD_MAP = {
    "烤": "roast",
    "烘烤": "bake",
    "水煮": "boil",
    "煮": "boil",
    "蒸": "steam",
    "烤架": "grill",
    "燒烤": "grill",
    "煎": "fry",
    "炸": "fry",
    "油炸": "fry",
    "燉": "stew",
    "燜": "braise",
    "炙烤": "broil",
    "微波": "microwave"
}


# =========================
# 載入 USDA 熟食營養
# =========================

with open(
    USDA_NUTRITION_PATH,
    "r",
    encoding="utf-8"
) as f:

    usda_cooked_foods = json.load(f)


# =========================
# 載入中文食材 Mapping
# =========================

with open(
    MAPPING_PATH,
    "r",
    encoding="utf-8"
) as f:

    food_mapping = json.load(f)


# =========================
# 中文料理方式標準化
# =========================

def normalize_cooking_method(
    cooking_method
):

    return METHOD_MAP.get(
        cooking_method,
        cooking_method.lower()
    )


# =========================
# 查 USDA 熟食營養
# =========================

def find_usda_cooked_nutrition(
    ingredient_name,
    cooking_method
):

    # ---------------------
    # 1. 找中文食材 mapping
    # ---------------------

    mapping = food_mapping.get(
        ingredient_name
    )

    if mapping is None:

        return {
            "success": False,
            "error":
                "usda_food_mapping_not_found",
            "ingredient":
                ingredient_name
        }


    required_keywords = [
        keyword.lower()
        for keyword
        in mapping.get(
            "usda_keywords",
            []
        )
    ]


    exclude_keywords = [
        keyword.lower()
        for keyword
        in mapping.get(
            "exclude_keywords",
            []
        )
    ]


    # ---------------------
    # 2. 標準化料理方式
    # ---------------------

    normalized_method = (
        normalize_cooking_method(
            cooking_method
        )
    )


    candidates = []


    # ---------------------
    # 3. 搜尋 USDA 熟食資料
    # ---------------------

    for food in usda_cooked_foods:

        description = (
            food.get(
                "description",
                ""
            )
            .lower()
        )

        food_method = food.get(
            "cooking_method"
        )


        # 所有 required keywords
        # 都必須存在
        keyword_match = all(
            keyword in description
            for keyword
            in required_keywords
        )


        # exclude keyword
        # 任何一個存在就排除
        exclude_match = any(
            keyword in description
            for keyword
            in exclude_keywords
        )


        # 料理方式必須一致
        method_match = (
            food_method
            == normalized_method
        )


        if (
            keyword_match
            and not exclude_match
            and method_match
        ):

            candidates.append(food)


    # =====================
    # 4. 完全找不到
    # =====================

    if len(candidates) == 0:

        return {
            "success": False,

            "error":
                "usda_cooked_nutrition_not_found",

            "ingredient":
                ingredient_name,

            "cooking_method":
                cooking_method,

            "normalized_method":
                normalized_method
        }


    # =====================
    # 5. 找到很多筆
    # 不可以隨便選第一筆
    # =====================

    if len(candidates) > 1:

        return {
            "success": False,

            "error":
                "multiple_usda_cooked_candidates",

            "ingredient":
                ingredient_name,

            "cooking_method":
                cooking_method,

            "candidate_count":
                len(candidates),

            "candidates": [
                {
                    "fdc_id":
                        item.get(
                            "fdc_id"
                        ),

                    "description":
                        item.get(
                            "description"
                        ),

                    "cooking_method":
                        item.get(
                            "cooking_method"
                        ),

                    "nutrition_complete":
                        item.get(
                            "nutrition_complete"
                        )
                }

                for item
                in candidates
            ]
        }


    # =====================
    # 6. 唯一結果
    # =====================

    food = candidates[0]


    return {
        "success": True,

        "ingredient":
            ingredient_name,

        "cooking_method":
            cooking_method,

        "normalized_method":
            normalized_method,

        "fdc_id":
            food.get(
                "fdc_id"
            ),

        "description":
            food.get(
                "description"
            ),

        "nutrition_per_100g":
            food.get(
                "nutrition_per_100g"
            ),

        "nutrition_complete":
            food.get(
                "nutrition_complete"
            ),

        "missing_fields":
            food.get(
                "missing_fields",
                []
            ),

        "source":
            food.get(
                "source"
            )
    }