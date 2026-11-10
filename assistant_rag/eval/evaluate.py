"""步驟 6：檢索評估與拒答門檻分析（R14 AC33／AC34）。

  python -m assistant_rag.eval.evaluate retrieval [--split dev]       # 11 組：Hit@1、Hit@5、MRR@5、延遲；存每題前 5 篇與最高分
  python -m assistant_rag.eval.evaluate threshold                     # 用開發題庫的結果選門檻（不碰驗證集）
  python -m assistant_rag.eval.evaluate holdout --method hybrid_rerank --embedder bge-m3 --threshold 0.5   # 驗證集只報告一次

- 檢索指標只算飲食題（expected_behavior=answer、非追問）；relevant_articles 任一篇出現在前 k 篇就算命中（文章層級）。
- 門檻只用重排分數（0–1）的方法；應回答＝飲食題，應拒答＝非飲食健康與無關題。醫療越界與寒暄由規則處理，不參與門檻。
- 類別回答（food_categories.json）：低於門檻時若問題提到對照表食材，會改查類別；分析時一併報告會因此改為回答的題數。
- 結果寫到 Data Root 的 eval/（含題目與文章標題，不含文章原文）。
"""
import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from assistant_rag import store
from assistant_rag.categories import FoodCategories
from assistant_rag.config import data_dir
from assistant_rag.retrieval import Retriever, combos, label

EVAL_DIR = Path(__file__).parent
RERANK_METHODS = ("hybrid_rerank", "entity_boost", "multi_query")
SHOULD_REFUSE = {"non_diet_health", "irrelevant"}


def load(split: str) -> list[dict]:
    return [json.loads(line) for line in (EVAL_DIR / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if line]


def rank_of(hits: list[str], relevant: set[str]) -> int | None:
    for i, key in enumerate(hits, 1):
        if key in relevant:
            return i
    return None


def metrics(ranks: list[int | None]) -> dict:
    n = len(ranks)
    return {"n": n, "hit1": round(sum(r == 1 for r in ranks) / n, 4), "hit5": round(sum(r is not None for r in ranks) / n, 4),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 4)}


def latency(values: list[float]) -> dict:
    s = sorted(values)
    return {"mean": round(statistics.mean(s), 3), "p50": round(statistics.median(s), 3),
            "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 3)}


def run_retrieval(conn, split: str, pairs, log=print) -> dict:
    questions = load(split)
    retriever = Retriever(conn)
    retriever.search("暖身", "hybrid_rerank", "bge-m3")  # 先載入模型，避免第一題延遲失真
    rows = []
    for q in questions:
        text = q.get("standalone") or q["question"]
        relevant = {a["article_key"] for a in q["relevant_articles"]}
        row = {"id": q["id"], "category": q["category"], "expected": q["expected_behavior"], "question": text,
               "relevant": sorted(relevant), "results": {}}
        for method, emb in pairs:
            t = time.perf_counter()
            res = retriever.search(text, method, emb)
            keys = [h.article_key for h in res.hits]
            row["results"][label(method, emb)] = {
                "top": [[h.article_key, h.title, round(h.score, 4)] for h in res.hits],
                "top_score": res.hits[0].score if res.hits else None,
                "rank": rank_of(keys, relevant) if relevant else None,
                "seconds": round(time.perf_counter() - t, 3), "extras": res.extras}
        rows.append(row)
        log(f"{q['id']} 完成")
    summary = {}
    scored = [r for r in rows if r["expected"] == "answer" and r["category"] == "diet"]
    for method, emb in pairs:
        name = label(method, emb)
        summary[name] = {**metrics([r["results"][name]["rank"] for r in scored]),
                         "latency": latency([r["results"][name]["seconds"] for r in rows])}
    return {"split": split, "version": retriever.version, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": summary, "rows": rows}


def threshold_table(rows: list[dict], name: str, categories: FoodCategories | None, category_scores: dict) -> dict:
    pos = [r for r in rows if r["expected"] == "answer" and r["category"] == "diet"]
    neg = [r for r in rows if r["category"] in SHOULD_REFUSE]
    ps = [r["results"][name]["top_score"] or 0 for r in pos]
    ns = [r["results"][name]["top_score"] or 0 for r in neg]
    candidates = sorted({0.0, *ps, *ns, 1.0})
    # 門檻落在相鄰兩個分數之間（lo, t]：正確數最多者優先，同分取最寬的區間，最後取區間中點留最大餘裕
    best = None
    for i, t in enumerate(candidates):
        lo = candidates[i - 1] if i else 0.0
        correct = sum(s >= t for s in ps) + sum(s < t for s in ns)
        key = (correct, t - lo)
        if best is None or key > best[0]:
            best = (key, t, lo)
    (correct, _), t, lo = best
    chosen = round((lo + t) / 2, 3) if lo < t else t

    def fallback(r, thr):
        fb = categories.lookup(r["question"]) if categories else None
        return bool(fb and category_scores.get(fb.category, 0) >= thr)

    return {"method": name, "positives": len(ps), "negatives": len(ns), "best_correct": correct,
            "threshold": chosen, "interval": [round(lo, 4), round(t, 4)],
            "min_positive": round(min(ps), 4), "max_negative": round(max(ns), 4),
            "gap": round(min(ps) - max(ns), 4),
            "positives_below": [r["id"] for r in pos if (r["results"][name]["top_score"] or 0) < chosen],
            "negatives_above": [r["id"] for r in neg if (r["results"][name]["top_score"] or 0) >= chosen],
            "positives_rescued_by_category": [r["id"] for r in pos if (r["results"][name]["top_score"] or 0) < chosen
                                              and fallback(r, chosen)],
            "negatives_answered_by_category": [r["id"] for r in neg if (r["results"][name]["top_score"] or 0) < chosen
                                               and fallback(r, chosen)]}


def category_scores(conn, emb: str, method: str) -> dict:
    cats = FoodCategories.load()
    retriever = Retriever(conn)
    return {k: (retriever.search(c["query"], method, emb).hits or [None])[0].score
            for k, c in cats.categories.items()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant_rag.eval.evaluate", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["retrieval", "threshold", "holdout"])
    parser.add_argument("--split", default="dev")
    parser.add_argument("--method", default="hybrid_rerank")
    parser.add_argument("--embedder", default="bge-m3")
    parser.add_argument("--threshold", type=float)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    out_dir = data_dir(None) / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        if args.command == "retrieval":
            if args.split != "dev":
                parser.error("驗證集請用 holdout 指令（只跑選定的一組）")
            result = run_retrieval(conn, "dev", combos(), log=lambda *_: None)
            (out_dir / "retrieval_dev.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(result["summary"], ensure_ascii=False, indent=1))
        elif args.command == "threshold":
            result = json.loads((out_dir / "retrieval_dev.json").read_text(encoding="utf-8"))
            cats = FoodCategories.load()
            tables = []
            for method, emb in combos():
                if method in RERANK_METHODS:
                    tables.append(threshold_table(result["rows"], label(method, emb), cats,
                                                  category_scores(conn, emb, method)))
            (out_dir / "threshold_dev.json").write_text(json.dumps(tables, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(tables, ensure_ascii=False, indent=1))
        else:
            if args.threshold is None:
                parser.error("holdout 需要 --threshold（開發題庫選定的值）")
            result = run_retrieval(conn, "holdout", [(args.method, args.embedder)], log=lambda *_: None)
            name = label(args.method, args.embedder)
            table = threshold_table(result["rows"], name, FoodCategories.load(),
                                    category_scores(conn, args.embedder, args.method))
            pos = [r for r in result["rows"] if r["expected"] == "answer" and r["category"] == "diet"]
            neg = [r for r in result["rows"] if r["category"] in SHOULD_REFUSE]
            t = args.threshold
            report = {"method": name, "threshold": t, "version": result["version"], "summary": result["summary"][name],
                      "answered_correctly": sum((r["results"][name]["top_score"] or 0) >= t for r in pos),
                      "refused_correctly": sum((r["results"][name]["top_score"] or 0) < t for r in neg),
                      "positives": len(pos), "negatives": len(neg),
                      "min_positive": table["min_positive"], "max_negative": table["max_negative"], "gap": table["gap"],
                      "positives_below": [r["id"] for r in pos if (r["results"][name]["top_score"] or 0) < t],
                      "negatives_above": [r["id"] for r in neg if (r["results"][name]["top_score"] or 0) >= t]}
            (out_dir / "holdout.json").write_text(json.dumps({"report": report, "rows": result["rows"]},
                                                             ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
