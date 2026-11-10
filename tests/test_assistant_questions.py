"""R14 步驟 5：題庫格式與配比（AC33）。只讀 assistant_rag/eval/*.jsonl，不連資料庫。"""
import json
import re
from collections import Counter
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "assistant_rag" / "eval"
BEHAVIORS = {"answer", "declined_medical", "emergency", "no_dose", "refuse", "greeting"}
CATEGORIES = {"diet", "followup", "medical", "non_diet_health", "irrelevant", "greeting"}
KEY = re.compile(r"^(healthnews|helloyishi):\d+$")


def load(name):
    return [json.loads(line) for line in (EVAL / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize("name", ["dev", "holdout"])
def test_items_well_formed(name):
    items = load(name)
    assert len({i["id"] for i in items}) == len(items)
    for item in items:
        assert item["category"] in CATEGORIES and item["expected_behavior"] in BEHAVIORS
        assert item["question"].strip() and item["review"] in {"draft", "approved", "changed", "added", "removed"}
        if item["category"] in ("diet", "followup"):
            assert item["expected_behavior"] == "answer" and KEY.match(item["primary_article"])
            keys = [a["article_key"] for a in item["relevant_articles"]]
            assert keys[0] == item["primary_article"] and len(keys) == len(set(keys))
            assert all(KEY.match(k) and a["title"] and a["url"].startswith("https://")
                       for k, a in zip(keys, item["relevant_articles"]))
        else:
            assert item["primary_article"] is None and item["relevant_articles"] == []
        if item["category"] == "followup":
            assert item["history"] and item["standalone"]


def test_mix_matches_ac33():
    dev, holdout = load("dev"), load("holdout")
    main = [i for i in dev if i["category"] != "followup"]
    counts = Counter(i["category"] for i in main)
    assert len(main) >= 100 and len(holdout) >= 50
    assert counts["diet"] >= 70 and counts["medical"] >= 10 and counts["non_diet_health"] >= 10
    assert counts["greeting"] == 3 and counts["irrelevant"] >= 5


def test_holdout_independent_from_dev():
    dev, holdout = load("dev"), load("holdout")
    assert not {i["question"] for i in dev} & {i["question"] for i in holdout}
    dev_primary = {i["primary_article"] for i in dev if i["primary_article"]}
    assert not dev_primary & {i["primary_article"] for i in holdout if i["primary_article"]}
