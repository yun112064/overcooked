import json
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).parent

RAW_DIR = (
    BASE_DIR
    / "data"
    / "raw"
    / "usda_sr_legacy"
)

FOOD_PATH = RAW_DIR / "food.csv"
FOOD_NUTRIENT_PATH = RAW_DIR / "food_nutrient.csv"
NUTRIENT_PATH = RAW_DIR / "nutrient.csv"

OUTPUT_PATH = (
    BASE_DIR
    / "data"
    / "usda_cooked_nutrition.json"
)


# =========================
# 我們專題需要的六項營養
# USDA FoodData Central nutrient ID
# =========================

TARGET_NUTRIENTS = {
    1008: "energy_kcal",
    1005: "carbohydrate_g",
    1003: "protein_g",
    1004: "fat_g",
    1093: "sodium_mg",
    1079: "dietary_fiber_g"
}


# =========================
# 用 USDA description 判斷料理方式
# =========================

COOKING_METHOD_KEYWORDS = {
    "roasted": "roast",
    "baked": "bake",
    "boiled": "boil",
    "steamed": "steam",
    "grilled": "grill",
    "fried": "fry",
    "broiled": "broil",
    "stewed": "stew",
    "braised": "braise",
    "microwaved": "microwave",
    "cooked": "cooked"
}


def detect_cooking_method(description):

    description = description.lower()

    # 先判斷明確料理方式
    for keyword, method in COOKING_METHOD_KEYWORDS.items():

        if keyword == "cooked":
            continue

        if keyword in description:
            return method

    # 只有 cooked，沒有更明確方式
    if "cooked" in description:
        return "cooked_unspecified"

    return None


def is_cooked_food(description):

    description = description.lower()

    cooked_keywords = [
        "cooked",
        "roasted",
        "baked",
        "boiled",
        "steamed",
        "grilled",
        "fried",
        "broiled",
        "stewed",
        "braised",
        "microwaved"
    ]

    return any(
        keyword in description
        for keyword in cooked_keywords
    )


def main():

    # =========================
    # 1. 確認檔案存在
    # =========================

    required_files = [
        FOOD_PATH,
        FOOD_NUTRIENT_PATH,
        NUTRIENT_PATH
    ]

    for path in required_files:

        if not path.exists():

            raise FileNotFoundError(
                f"找不到檔案：{path}"
            )


    # =========================
    # 2. 讀取 CSV
    # =========================

    print("讀取 USDA CSV...")

    food_df = pd.read_csv(
        FOOD_PATH,
        low_memory=False
    )

    food_nutrient_df = pd.read_csv(
        FOOD_NUTRIENT_PATH,
        low_memory=False
    )

    nutrient_df = pd.read_csv(
        NUTRIENT_PATH,
        low_memory=False
    )


    print(
        "food rows:",
        len(food_df)
    )

    print(
        "food_nutrient rows:",
        len(food_nutrient_df)
    )


    # =========================
    # 3. 篩選熟食
    # =========================

    cooked_food_df = food_df[
        food_df["description"]
        .fillna("")
        .apply(is_cooked_food)
    ].copy()


    print(
        "熟食數量:",
        len(cooked_food_df)
    )


    # =========================
    # 4. 只保留六項營養
    # =========================

    target_nutrient_df = (
        food_nutrient_df[
            food_nutrient_df[
                "nutrient_id"
            ].isin(
                TARGET_NUTRIENTS.keys()
            )
        ]
        .copy()
    )


    # =========================
    # 5. 與熟食資料合併
    # =========================

    merged_df = target_nutrient_df.merge(
        cooked_food_df[
            [
                "fdc_id",
                "description"
            ]
        ],
        on="fdc_id",
        how="inner"
    )


    # =========================
    # 6. 建立 JSON
    # =========================

    results = []


    for fdc_id, group in merged_df.groupby(
        "fdc_id"
    ):

        description = (
            group.iloc[0]["description"]
        )

        nutrition = {
            "energy_kcal": None,
            "carbohydrate_g": None,
            "protein_g": None,
            "fat_g": None,
            "sodium_mg": None,
            "dietary_fiber_g": None
        }


        # 將 USDA nutrient_id
        # 對應到我們自己的欄位
        for _, row in group.iterrows():

            nutrient_id = int(
                row["nutrient_id"]
            )

            field_name = (
                TARGET_NUTRIENTS.get(
                    nutrient_id
                )
            )

            if field_name is None:
                continue

            amount = row["amount"]

            if pd.isna(amount):
                continue

            nutrition[field_name] = round(
                float(amount),
                2
            )


        cooking_method = (
            detect_cooking_method(
                description
            )
        )


        missing_fields = [
            key
            for key, value
            in nutrition.items()
            if value is None
        ]


        results.append(
            {
                "fdc_id": int(fdc_id),

                "description":
                    description,

                "state":
                    "cooked",

                "cooking_method":
                    cooking_method,

                "nutrition_per_100g":
                    nutrition,

                "nutrition_complete":
                    len(
                        missing_fields
                    ) == 0,

                "missing_fields":
                    missing_fields,

                "source": {
                    "provider":
                        "USDA FoodData Central",

                    "data_type":
                        "SR Legacy",

                    "release":
                        "April 2018"
                }
            }
        )


    # =========================
    # 7. 輸出 JSON
    # =========================

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )


    with open(
        OUTPUT_PATH,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            results,
            f,
            ensure_ascii=False,
            indent=2
        )


    print()
    print(
        "完成！輸出：",
        OUTPUT_PATH
    )

    print(
        "共建立",
        len(results),
        "筆熟食資料"
    )


if __name__ == "__main__":
    main()