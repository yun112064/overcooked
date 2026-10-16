import pandas as pd
import json
from pathlib import Path


BASE_DIR = Path(__file__).parent

EXCEL_PATH = (
    BASE_DIR
    / "data"
    / "raw"
    / "USDA_CookingYields_MeatPoultry02.xlsx"
)

OUTPUT_PATH = (
    BASE_DIR
    / "data"
    / "cooking_yields.json"
)


def normalize(value):
    if pd.isna(value):
        return ""

    return " ".join(
        str(value)
        .replace("\n", " ")
        .strip()
        .split()
    )


def find_header_row(df):
    for i in range(len(df)):

        row_text = " | ".join(
            normalize(v).lower()
            for v in df.iloc[i].tolist()
        )

        if (
            "yield description" in row_text
            and "preparation method" in row_text
            and "cooking yield" in row_text
        ):
            return i

    return None


def find_column(columns, *keywords):

    for col in columns:

        normalized = normalize(col).lower()

        if all(
            keyword.lower() in normalized
            for keyword in keywords
        ):
            return col

    return None


records = []

excel_file = pd.ExcelFile(
    EXCEL_PATH,
    engine="openpyxl"
)


for sheet_name in excel_file.sheet_names:

    raw_df = pd.read_excel(
        EXCEL_PATH,
        sheet_name=sheet_name,
        engine="openpyxl",
        header=None
    )

    header_row = find_header_row(raw_df)

    if header_row is None:
        continue


    df = pd.read_excel(
        EXCEL_PATH,
        sheet_name=sheet_name,
        engine="openpyxl",
        header=header_row
    )


    description_col = find_column(
        df.columns,
        "yield",
        "description"
    )

    method_col = find_column(
        df.columns,
        "preparation",
        "method"
    )

    yield_col = find_column(
        df.columns,
        "cooking",
        "yield"
    )


    if not all([
        description_col,
        method_col,
        yield_col
    ]):
        continue


    for _, row in df.iterrows():

        cooking_yield = pd.to_numeric(
            row[yield_col],
            errors="coerce"
        )

        if pd.isna(cooking_yield):
            continue


        records.append({
            "yield_description":
                normalize(row[description_col]),

            "preparation_method":
                normalize(row[method_col]),

            "cooking_yield_pct":
                float(cooking_yield),

            "source":
                "USDA Table of Cooking Yields "
                "for Meat and Poultry, Release 2"
        })


with open(
    OUTPUT_PATH,
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        records,
        f,
        ensure_ascii=False,
        indent=2
    )


print(
    f"完成，共匯入 {len(records)} 筆 cooking yield"
)