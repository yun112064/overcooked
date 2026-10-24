"""Import the project's recipe workbook into the existing six-table schema.

Run from repository root:
    python scripts/import_recipes.py --file local_data/recipe_dataset_1016_combine_3.xlsx
    python scripts/import_recipes.py --file local_data/recipe_dataset_1016_combine_3.xlsx --apply

Requires only the team's SQLAlchemy + psycopg dependencies. No DB writes unless --apply.
Only imports to entirely empty recipe tables. Never deletes/replaces existing records.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from sqlalchemy import MetaData, create_engine, select, func, insert

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import settings  # noqa: E402

NS = {
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
}

# (Excel worksheet, Postgres table), in foreign-key-safe insertion order.
SHEETS = [
    ("source_archive", "recipe_source_archive"),
    ("recipes", "recipes"),
    ("recipe_ingredients", "recipe_ingredients"),
    ("recipe_steps", "recipe_steps"),
    ("ingredient_aliases", "recipe_ingredient_aliases"),
    ("allergens", "recipe_allergens"),
]
NUMBER_COLUMNS = {"amount", "weight_g", "protein_g", "carbohydrates_g", "fat_g", "sugar_g", "sodium_mg"}
INT_COLUMNS = {"sequence", "step_no"}


def read_workbook(filename: Path) -> dict[str, list[dict]]:
    """Read plain values from .xlsx using stdlib; no Excel formulas are executed."""
    with ZipFile(filename) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(n.text or "" for n in si.iter("{%s}t" % NS["x"])) for si in root]
        book = ET.fromstring(z.read("xl/workbook.xml"))
        relroot = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rels = {r.attrib["Id"]: r.attrib["Target"] for r in relroot}
        paths = {}
        for s in book.findall("x:sheets/x:sheet", NS):
            path = rels[s.attrib["{%s}id" % NS["r"]]]
            paths[s.attrib["name"]] = (path.lstrip("/") if path.startswith("/") else "xl/" + path)
        result = {}
        for sheet, _ in SHEETS:
            if sheet not in paths:
                raise ValueError(f"Excel 缺少工作表：{sheet}")
            root = ET.fromstring(z.read(paths[sheet]))
            rows = []
            for row in root.findall("x:sheetData/x:row", NS):
                cells = {}
                for c in row.findall("x:c", NS):
                    ref = c.attrib["r"]
                    letters = "".join(x for x in ref if x.isalpha())
                    index = 0
                    for x in letters:
                        index = index * 26 + ord(x.upper()) - ord("A") + 1
                    v = c.find("x:v", NS)
                    if c.attrib.get("t") == "inlineStr":
                        node = c.find("x:is", NS)
                        value = "".join(t.text or "" for t in node.iter("{%s}t" % NS["x"])) if node is not None else None
                    elif v is None:
                        value = None
                    elif c.attrib.get("t") == "s":
                        value = shared[int(v.text)]
                    else:
                        value = v.text
                    cells[index - 1] = value
                if cells:
                    rows.append(cells)
            if not rows:
                raise ValueError(f"空白工作表：{sheet}")
            header = {i: str(v).strip().lower() for i, v in rows[0].items() if v is not None and str(v).strip()}
            data = []
            for excel_row, cells in enumerate(rows[1:], start=2):
                record = {name: cells.get(i) for i, name in header.items()}
                if any(v is not None and str(v).strip() for v in record.values()):
                    record["_excel_row"] = excel_row
                    data.append(record)
            result[sheet] = data
        return result


def checked_value(col, value, table, excel_row):
    if value is None or str(value).strip() == "":
        return None
    value = str(value).strip()
    if col in INT_COLUMNS:
        try:
            parsed = Decimal(value)
            if parsed != parsed.to_integral_value():
                raise ValueError()
            return int(parsed)
        except (ValueError, InvalidOperation):
            raise ValueError(f"{table} Excel 第 {excel_row} 列：{col} 必須是整數")
    if col in NUMBER_COLUMNS:
        try:
            val = Decimal(value)
            if not val.is_finite():
                raise ValueError()
            return val
        except (InvalidOperation, ValueError):
            raise ValueError(f"{table} Excel 第 {excel_row} 列：{col} 不是有效數字")
    return value


def prepare(source, metadata):
    output = {}
    all_ids = {}
    for sheet, table_name in SHEETS:
        t = metadata.tables[table_name]
        accepted = {c.name for c in t.columns}
        primary = [c.name for c in t.primary_key.columns]
        transformed = []
        ids = set()
        ordering = set()
        for source_row in source[sheet]:
            excel_row = source_row["_excel_row"]
            extra = set(source_row) - accepted - {"_excel_row"}
            if extra:
                raise ValueError(f"{sheet} 發現資料庫沒有的欄位：{sorted(extra)}")
            row = {k: checked_value(k, v, sheet, excel_row) for k, v in source_row.items() if k in accepted}
            for c in t.columns:
                if not c.nullable and row.get(c.name) is None:
                    raise ValueError(f"{sheet} 第 {excel_row} 列缺少必要欄位 {c.name}")
                if row.get(c.name) is not None and getattr(c.type, "length", None) and len(str(row[c.name])) > c.type.length:
                    raise ValueError(f"{sheet} 第 {excel_row} 列 {c.name} 超過長度上限")
            key = tuple(row.get(k) for k in primary)
            if key in ids:
                raise ValueError(f"{sheet} 重複主鍵 {key}")
            ids.add(key)
            if table_name in {"recipe_ingredients", "recipe_steps"}:
                number_field = "sequence" if table_name == "recipe_ingredients" else "step_no"
                seq = (row["recipe_id"], row[number_field])
                if seq in ordering:
                    raise ValueError(f"{sheet} 重複順序 {seq}")
                ordering.add(seq)
            transformed.append(row)
        output[table_name] = transformed
        all_ids[table_name] = {k[0] for k in ids}
    for row in output["recipes"]:
        if row.get("raw_id") and row["raw_id"] not in all_ids["recipe_source_archive"]:
            raise ValueError(f"recipes {row['recipe_id']} 對應的 raw_id 不存在")
    for table in ("recipe_ingredients", "recipe_steps"):
        for row in output[table]:
            if row["recipe_id"] not in all_ids["recipes"]:
                raise ValueError(f"{table} 對應的 recipe_id 不存在：{row['recipe_id']}")
    return output


def main():
    parser = argparse.ArgumentParser(description="Safely import recipe Excel into existing Postgres tables")
    parser.add_argument("--file", type=Path, default=ROOT / "local_data" / "recipe_dataset_1016_combine_3.xlsx")
    parser.add_argument("--apply", action="store_true", help="Actually write data (default: preflight only)")
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error(f"找不到 Excel：{args.file}")
    source = read_workbook(args.file)
    # Connection only after Excel is successfully readable.
    engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        meta = MetaData()
        meta.reflect(bind=engine, only=[table for _, table in SHEETS])
        records = prepare(source, meta)
        print("Excel 預檢通過：")
        for table, rows in records.items():
            print(f"  {table}: {len(rows)} 筆")
        with engine.begin() as connection:
            existing = {table: connection.execute(select(func.count()).select_from(meta.tables[table])).scalar_one() for table in records}
            if any(existing.values()):
                raise ValueError(f"資料庫已有食譜資料 {existing}；為避免覆寫與重複匯入，已停止。")
            if not args.apply:
                print("預檢模式，沒有寫入資料。如需正式匯入，請加 --apply。")
                return
            for table, rows in records.items():
                if rows:
                    connection.execute(insert(meta.tables[table]), rows)
        print("匯入成功：六張資料表已在同一個交易中提交。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as e:
        print(f"匯入停止：{e}", file=sys.stderr)
        sys.exit(1)
