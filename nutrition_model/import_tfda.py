import json
import re
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).parent

CSV_PATH = (
    BASE_DIR
    / "data"
    / "raw"
    / "tfda"
    / "nutrition_ingredient.csv"
)

OUTPUT_PATH = (
    BASE_DIR
    / "data"
    / "ingredient_master.json"
)


# =========================
# 我們需要的六項營養
# TFDA 分析項名稱 → 系統欄位
# =========================

NUTRIENT_MAP = {
    "熱量": "energy_kcal",

    "總碳水化合物": "carbohydrate_g",
    "碳水化合物": "carbohydrate_g",

    "粗蛋白": "protein_g",
    "蛋白質": "protein_g",

    "粗脂肪": "fat_g",
    "脂肪": "fat_g",

    "鈉": "sodium_mg",

    "膳食纖維": "dietary_fiber_g"
}


EXPECTED_UNITS = {
    "energy_kcal": ["kcal", "大卡"],
    "carbohydrate_g": ["g", "公克"],
    "protein_g": ["g", "公克"],
    "fat_g": ["g", "公克"],
    "sodium_mg": ["mg", "毫克"],
    "dietary_fiber_g": ["g", "公克"]
}


# =========================
# 讀取 TFDA CSV
# =========================

def read_tfda_csv(path):

    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp950",
        "big5"
    ]

    last_error = None

    for encoding in encodings:

        try:

            print(
                f"嘗試使用編碼：{encoding}"
            )

            return pd.read_csv(
                path,
                encoding=encoding,
                low_memory=False
            )

        except UnicodeDecodeError as e:

            last_error = e

    raise last_error


# =========================
# 數值轉換
# =========================

def parse_number(value):

    if pd.isna(value):
        return None

    text = str(value).strip()

    missing_values = {
        "",
        "-",
        "--",
        "NA",
        "N/A",
        "ND",
        "Tr",
        "TR",
        "微量"
    }

    if text in missing_values:
        return None

    text = text.replace(",", "")

    try:
        return float(text)

    except ValueError:
        return None


# =========================
# 俗名 / alias 解析
# =========================

def parse_aliases(value):

    if pd.isna(value):
        return []

    text = str(value).strip()

    if not text:
        return []

    parts = re.split(
        r"[、,，;/；]+",
        text
    )

    aliases = []

    for part in parts:

        alias = part.strip()

        if alias:
            aliases.append(alias)

    return list(
        dict.fromkeys(
            aliases
        )
    )


# =========================
# 確認營養單位
# =========================

def unit_matches(
    field_name,
    unit
):

    if pd.isna(unit):
        return True

    unit_text = str(
        unit
    ).strip()

    expected = EXPECTED_UNITS.get(
        field_name,
        []
    )

    return (
        unit_text in expected
        or unit_text.lower()
        in [
            item.lower()
            for item in expected
        ]
    )


# =========================
# 判斷食品生 / 熟狀態
# =========================

def detect_food_state(
    description
):

    if pd.isna(description):
        return "unspecified"

    text = str(
        description
    ).strip()

    # 移除所有空白
    normalized_text = re.sub(
        r"\s+",
        "",
        text
    )

    # TFDA 明確標示「樣品狀態：生」
    if (
        "樣品狀態:生"
        in normalized_text
        or
        "樣品狀態：生"
        in normalized_text
    ):
        return "raw"

    # TFDA 明確標示「樣品狀態：熟」
    if (
        "樣品狀態:熟"
        in normalized_text
        or
        "樣品狀態：熟"
        in normalized_text
    ):
        return "cooked"

    return "unspecified"


# =========================
# 主程式
# =========================

def main():

    # =========================
    # 1. 確認 CSV 是否存在
    # =========================

    if not CSV_PATH.exists():

        raise FileNotFoundError(
            f"找不到 TFDA CSV：{CSV_PATH}"
        )


    # =========================
    # 2. 讀取 TFDA CSV
    # =========================

    df = read_tfda_csv(
        CSV_PATH
    )


    print()

    print(
        "CSV 資料筆數：",
        len(df)
    )

    print()

    print(
        "欄位："
    )

    print(
        df.columns.tolist()
    )


    # =========================
    # 3. 確認必要欄位
    # =========================

    required_columns = [
        "整合編號",
        "樣品名稱",
        "分析項",
        "含量單位",
        "每100克含量"
    ]

    missing_columns = [
        column
        for column
        in required_columns
        if column
        not in df.columns
    ]

    if missing_columns:

        raise ValueError(
            "TFDA CSV 缺少欄位："
            + ", ".join(
                missing_columns
            )
        )


    # =========================
    # 4. 依整合編號分組
    # =========================

    ingredients = []


    for ingredient_id, group in (
        df.groupby(
            "整合編號",
            dropna=True
        )
    ):

        first_row = group.iloc[0]


        # =====================
        # 食材名稱
        # =====================

        standard_name = str(
            first_row[
                "樣品名稱"
            ]
        ).strip()


        # =====================
        # Alias / 俗名
        # =====================

        aliases = []

        if "俗名" in group.columns:

            aliases = parse_aliases(
                first_row.get(
                    "俗名"
                )
            )


        # 避免 alias 與正式名稱相同
        aliases = [
            alias
            for alias
            in aliases
            if alias
            != standard_name
        ]


        # =====================
        # 六項營養初始化
        # =====================

        nutrition = {
            "energy_kcal": None,
            "carbohydrate_g": None,
            "protein_g": None,
            "fat_g": None,
            "sodium_mg": None,
            "dietary_fiber_g": None
        }


        # =====================
        # 5. 找六項營養
        # =====================

        for _, row in (
            group.iterrows()
        ):

            analysis_item = str(
                row[
                    "分析項"
                ]
            ).strip()

            field_name = (
                NUTRIENT_MAP.get(
                    analysis_item
                )
            )

            if field_name is None:
                continue


            unit = row[
                "含量單位"
            ]

            if not unit_matches(
                field_name,
                unit
            ):
                continue


            amount = parse_number(
                row[
                    "每100克含量"
                ]
            )

            if amount is None:
                continue


            nutrition[
                field_name
            ] = amount


        # =====================
        # 缺少哪些營養
        # =====================

        missing_fields = [
            key
            for key, value
            in nutrition.items()
            if value is None
        ]


        # =====================
        # 取得 description
        # =====================

        description = None

        if (
            "內容物描述"
            in group.columns
        ):

            raw_description = (
                first_row.get(
                    "內容物描述"
                )
            )

            if pd.notna(
                raw_description
            ):

                description = str(
                    raw_description
                ).strip()


        # =====================
        # 生 / 熟狀態
        # =====================

        state = detect_food_state(
            description
        )


        # =====================
        # 建立 ingredient
        # =====================

        ingredient = {

            "ingredient_id":
                str(
                    ingredient_id
                ),

            "standard_name":
                standard_name,

            "aliases":
                aliases,

            "state":
                state,

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
                    "TFDA",

                "source_id":
                    str(
                        ingredient_id
                    ),

                "source_file":
                    "nutrition_ingredient.csv"
            }
        }


        # =====================
        # 英文名稱
        # =====================

        if (
            "樣品英文名稱"
            in group.columns
        ):

            english_name = (
                first_row.get(
                    "樣品英文名稱"
                )
            )

            if pd.notna(
                english_name
            ):

                ingredient[
                    "english_name"
                ] = str(
                    english_name
                ).strip()


        # =====================
        # Description
        # =====================

        if description:

            ingredient[
                "description"
            ] = description


        ingredients.append(
            ingredient
        )


    # =========================
    # 6. 輸出 ingredient_master.json
    # =========================

    with open(
        OUTPUT_PATH,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            ingredients,
            f,
            ensure_ascii=False,
            indent=2
        )


    # =========================
    # 統計
    # =========================

    complete_count = sum(
        1
        for item
        in ingredients
        if item[
            "nutrition_complete"
        ]
    )


    raw_count = sum(
        1
        for item
        in ingredients
        if item[
            "state"
        ] == "raw"
    )


    cooked_count = sum(
        1
        for item
        in ingredients
        if item[
            "state"
        ] == "cooked"
    )


    unspecified_count = sum(
        1
        for item
        in ingredients
        if item[
            "state"
        ] == "unspecified"
    )


    print()

    print(
        "完成！"
    )

    print(
        "輸出檔案：",
        OUTPUT_PATH
    )

    print(
        "食品數量：",
        len(
            ingredients
        )
    )

    print(
        "六項營養完整：",
        complete_count
    )

    print(
        "有缺值：",
        len(
            ingredients
        )
        - complete_count
    )

    print()

    print(
        "raw：",
        raw_count
    )

    print(
        "cooked：",
        cooked_count
    )

    print(
        "unspecified：",
        unspecified_count
    )


if __name__ == "__main__":
    main()