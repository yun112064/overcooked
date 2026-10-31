"""決策 #38：把食譜分類修正表套用到已匯入的資料庫（不必重新匯入工作簿）。

python -m app.jobs.apply_category_overrides [--file config/recipe_category_overrides.json] [--dry-run]

連線使用 DATABASE_URL。冪等：已是修正後分類的食譜列為 unchanged；印出每筆變更（from → to、理由）。
"""
import argparse
import json

from app.services.recipe_import import CATEGORY_OVERRIDES_PATH, apply_category_overrides, load_category_overrides


def run(factory, path=CATEGORY_OVERRIDES_PATH, *, dry_run: bool = False) -> dict:
    overrides = load_category_overrides(path)
    with factory() as db, db.begin():
        return apply_category_overrides(db, overrides, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply config/recipe_category_overrides.json to the recipes table")
    parser.add_argument("--file", default=str(CATEGORY_OVERRIDES_PATH))
    parser.add_argument("--dry-run", action="store_true", help="列出會變更的食譜，不寫入資料庫")
    args = parser.parse_args()
    from app.db.session import SessionLocal  # 讀 DATABASE_URL；放在參數解析後，--help 不需要連線設定

    result = run(SessionLocal, args.file, dry_run=args.dry_run)
    for change in result["changed"]:
        print(f"{'[dry-run] ' if args.dry_run else ''}{change['recipe_id']}: {change['from']} → {change['to']}"
              f"（{change['reason']}）")
    print(json.dumps({"changed": len(result["changed"]), "unchanged": len(result["unchanged"]),
                      "missing_recipe_ids": result["missing_recipe_ids"], "dry_run": args.dry_run},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
