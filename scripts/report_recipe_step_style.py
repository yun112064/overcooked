"""Render a step wording report from a read-only recipe inventory exported as JSONL."""

import argparse
from collections import Counter
from html import escape
from itertools import groupby
import json
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.recipe_step_style import STYLE_VERSION, check_recipe_steps


def read_inventory(path: Path):
    with path.open(encoding="utf-8-sig") as source:
        meta = json.loads(next(source))
        rows = [json.loads(line) for line in source if line.strip()]
    if meta["read_only"] != "on" or len(rows) != meta["step_count"]:
        raise ValueError("The inventory must contain every step from a read-only transaction.")
    return meta, sorted(rows, key=lambda row: (row["recipe_id"], row["step_no"], row["step_id"]))


def collect_findings(rows):
    findings = []
    for _, group in groupby(rows, key=lambda row: row["recipe_id"]):
        steps = list(group)
        result = check_recipe_steps((step["step_no"], step["text"]) for step in steps)
        by_number = {step["step_no"]: step for step in steps}
        if len(by_number) != len(steps):
            raise ValueError(f"Duplicate step number in {steps[0]['recipe_id']}")
        for finding in result["findings"]:
            row = by_number[finding["step_no"]]
            findings.append({**finding, **{key: row[key] for key in ("recipe_id", "recipe_name", "step_id")}})
    return findings


def section(label, section_id, findings):
    groups = []
    for _, group in groupby(findings, key=lambda row: row["recipe_id"]):
        rows = list(group)
        recipe = rows[0]
        table_rows = []
        for row in rows:
            original = escape(row["original"])
            suggestion = escape(row["suggestion"] or "待確認後改寫")
            reasons = "<br>".join(escape(reason) for reason in row["reasons"])
            table_rows.append(
                f"<tr><th scope='row'>第 {row['step_no']} 步<br>{escape(row['step_id'])}</th>"
                f"<td>{original}</td><td>{suggestion}</td><td>{reasons}</td></tr>"
            )
        groups.append(
            f"<details><summary>{escape(recipe['recipe_id'])} {escape(recipe['recipe_name'])} · {len(rows)} 步</summary>"
            "<div class='table'><table><thead><tr><th scope='col'>步驟</th><th scope='col'>原文</th>"
            "<th scope='col'>建議改寫</th><th scope='col'>判斷依據</th></tr></thead>"
            "<tbody>" + "".join(table_rows) + "</tbody></table></div></details>"
        )
    return (
        f"<section id='{section_id}'><h2>{label}（{len(findings):,} 步）</h2>"
        + ("".join(groups) if groups else "<p>本次掃描沒有符合的步驟。</p>")
        + "</section>"
    )


def write_report(output: Path, meta, findings):
    counts = Counter(row["status"] for row in findings)
    pending = [row for row in findings if row["status"] == "needs_review"]
    suggested = [row for row in findings if row["status"] == "suggested"]
    sections = section("待人工確認", "review", pending) + section("建議改寫", "suggested", suggested)
    html = f"""<!doctype html>
<html lang="zh-Hant">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>食譜步驟用詞檢查</title>
<style>
body {{ margin: 2rem auto; padding: 0 1rem; max-width: 72rem; font: 1rem/1.6 system-ui, sans-serif; color: #182230; }}
h1 {{ margin-bottom: .4rem; }} p {{ margin: .45rem 0; }}
nav {{ display: flex; gap: 1.25rem; margin-top: 1.25rem; }}
summary {{ cursor: pointer; padding: .5rem .65rem; background: #eef3f8; }}
details {{ margin: .3rem 0; }}
.table {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; min-width: 42rem; }}
th, td {{ text-align: left; vertical-align: top; padding: .5rem; border-bottom: 1px solid #dce3e9; }}
td {{ white-space: pre-wrap; }}
th:first-child {{ width: 7.2rem; }} th:last-child {{ width: 13rem; }}
</style>
<main>
<h1>食譜步驟用詞檢查</h1>
<p>食譜版本 <code>{escape(meta["release"])}</code> · 規則 <code>{STYLE_VERSION}</code></p>
<p>唯讀掃描 {meta["recipe_count"]:,} 道食譜、{meta["step_count"]:,} 筆步驟。{counts["suggested"]:,} 步有改寫建議，
{counts["needs_review"]:,} 步待人工確認，{meta["step_count"] - len(findings):,} 步沒有觸發本次規則。</p>
<p>點選菜名可查看逐步對照。待人工確認的步驟保留原文，待核對上下文後再提出改寫。</p>
<nav aria-label="報告分類"><a href="#review">待人工確認</a><a href="#suggested">建議改寫</a></nav>
{sections}
</main>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return {"recipe_count": meta["recipe_count"], "step_count": meta["step_count"],
            "suggested": counts["suggested"], "needs_review": counts["needs_review"],
            "unflagged": meta["step_count"] - len(findings),
            "affected_recipes": len({row["recipe_id"] for row in findings}),
            "review_recipes": len({row["recipe_id"] for row in pending})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    meta, rows = read_inventory(args.inventory)
    print(json.dumps(write_report(args.output, meta, collect_findings(rows))))


if __name__ == "__main__":
    main()
