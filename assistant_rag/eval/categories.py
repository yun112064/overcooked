"""類別回答與同物異名重查的品質評估（R14；開發／驗證題庫都沒有缺口食材題，所以另外抽食材）。

  python -m assistant_rag.eval.categories run      # 抽食材 → 完整回答流程 → 檢查 → RAGAS 定義指標

抽樣：food_categories.json 每個類別取 V3 食譜中出現最多次的 2 種食材，加上所有有別名的食材與 refuse 食材
（refuse 預期拒答，除非本名就查得到）；
問句固定為「{食材}有什麼營養？吃了有什麼好處」。這些食材是覆蓋檢查中查不到專文（或文章沒提到）的，
所以預期是類別回答或別名重查後直接回答；直接回答代表本名就查得到，另外列出供人工確認。

檢查（每則）：
- route：answer／category_answer／out_of_scope；別名重查記在 alias。
- notice_ok：類別回答開頭是否為固定說明且食材名稱正確；別名回答開頭是否說明同一種食物。
- food_named：正文（去掉開頭說明）有幾句提到食材名稱——提示要求用「這類食物」當主詞，提到不一定錯，但要人工看。
- 規則檢查與 RAGAS 定義的忠實度／回答相關性，與 eval.answers 相同。
結果寫到 Data Root 的 eval/categories.json（含回答原文，不進 Git）。
"""
import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from assistant_rag import store
from assistant_rag.answer import Assistant, Settings
from assistant_rag.categories import FoodCategories
from assistant_rag.config import PROJECT_ROOT, data_dir
from assistant_rag.eval.answers import body, ragas, rule_check
from assistant_rag.retrieval import Retriever

WORKBOOK = PROJECT_ROOT / "data" / "recipe_dataset_1103_V3.xlsx"
TEMPLATE = "{food}有什麼營養？吃了有什麼好處"
PER_CATEGORY = 2


def recipe_counts(path: Path = WORKBOOK) -> dict[str, int]:
    """V3 食譜中每種食材出現在幾道食譜（抽樣排序用）；試算表不在時全部視為 0。"""
    if not path.exists():
        return {}
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    rows = list(wb["recipe_ingredients"].iter_rows(values_only=True))
    head = rows[0]
    name, recipe = head.index("canonical_name"), head.index("recipe_id")
    seen = collections.defaultdict(set)
    for r in rows[1:]:
        if r[name]:
            seen[r[name]].add(r[recipe])
    return {k: len(v) for k, v in seen.items()}


def sample(cats: FoodCategories, counts: dict[str, int], per_category: int = PER_CATEGORY) -> list[dict]:
    by_cat = collections.defaultdict(list)
    for food, key in cats.foods.items():
        by_cat[key].append(food)
    picked = []
    for key in sorted(by_cat):
        foods = sorted(by_cat[key], key=lambda f: (-counts.get(f, 0), f))[:per_category]
        picked += [{"food": f, "category": key, "reason": "category"} for f in foods]
    have = {p["food"] for p in picked}
    picked += [{"food": f, "category": cats.foods[f], "reason": "alias"} for f in sorted(cats.aliases)
               if f not in have and f in cats.foods]
    picked += [{"food": f, "category": None, "reason": "refuse"} for f in sorted(cats.refuse)]
    for p in picked:
        p["recipes"] = counts.get(p["food"], 0)
        p["question"] = TEMPLATE.format(food=p["food"])
    return picked


NOTICE_ALIAS = re.compile(r"^「([^」]+)」和「([^」]+)」是同一種食物，以下以「[^」]+」的資料回答：\n")


def check(item: dict, out: dict) -> dict:
    text, food = out["text"], item["food"]
    alias = (out["done"].get("alias") or {}) if isinstance(out.get("done"), dict) else {}
    if out["type"] == "category_answer":
        notice_ok = text.startswith(f"目前沒有「{food}」本身的資料，以下是「")
    elif text.startswith("「"):
        m = NOTICE_ALIAS.match(text)
        notice_ok = bool(m and m.group(1) == food)
    else:
        notice_ok = None  # 直接回答沒有開頭說明
    main = NOTICE_ALIAS.sub("", body(text))
    sentences = [s for s in re.split(r"(?<=[。！？!?])|\n", main) if s.strip()]
    named = [s.strip() for s in sentences if food in s]
    return {"notice_ok": notice_ok, "food_named": named, "alias": alias.get("alias")}


def run(threshold: float = 0.5, log=print) -> dict:
    os.environ.setdefault("RAG_THRESHOLD_STATUS", "evaluation")
    cats = FoodCategories.load()
    items = sample(cats, recipe_counts())
    rows = []
    with store.connect() as conn:
        retriever = Retriever(conn)
        bot = Assistant(retriever, settings=Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=threshold))
        for i, item in enumerate(items, 1):
            out = bot.answer(item["question"])
            ids = [r["chunk_id"] for r in out["references"]]
            details = store.chunk_details(conn, retriever.version, ids) if ids else {}
            rows.append({**item, "id": f"C{i:03d}", "type": out["type"], "error": out["error"], "text": out["text"],
                         "references": [{"n": r["n"], "title": r["title"],
                                         "article_key": details.get(r["chunk_id"], {}).get("article_key"),
                                         "text": details.get(r["chunk_id"], {}).get("text", "")} for r in out["references"]],
                         "done": out["done"], **rule_check(out), **check(item, out)})
            log(f"{rows[-1]['id']} {item['food']} {out['type']}")
        version = retriever.version
    return {"version": version, "threshold": threshold, "template": TEMPLATE,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summarize(rows), "rows": rows}


def summarize(rows: list[dict]) -> dict:
    types = collections.Counter(r["type"] for r in rows)
    issues = collections.Counter(kind for r in rows for kind, _ in r["issues"])
    gen = [r for r in rows if r["type"] in ("answer", "category_answer")]
    return {"n": len(rows), "types": dict(types), "alias_answers": sum(bool(r["alias"]) for r in rows),
            "notice_ok": sum(r["notice_ok"] is True for r in rows), "notice_checked": sum(r["notice_ok"] is not None for r in rows),
            "food_named_answers": sum(bool(r["food_named"]) for r in gen), "issues": dict(issues),
            "errors": sum(bool(r["error"]) for r in rows)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant_rag.eval.categories")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--no-ragas", action="store_true")
    args = parser.parse_args(argv)
    result = run(args.threshold)
    if not args.no_ragas:
        result = ragas(result)
    out = data_dir() / "eval" / "categories.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "ragas": result.get("ragas")}, ensure_ascii=False, indent=1))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
