from pathlib import Path
import pandas as pd


BASE_DIR = Path(__file__).parent

FILE_PATH = (
    BASE_DIR
    / "data"
    / "raw"
    / "E0200101_2026_10_03.xlsx"
)


df = pd.read_excel(FILE_PATH)


print("欄位：")
print(df.columns.tolist())

print()
print("資料筆數：", len(df))

print()
print("前 5 筆：")
print(df.head())