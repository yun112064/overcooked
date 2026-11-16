"""R14 步驟 6：評估程式的計算（合成資料，不連資料庫、不呼叫模型）。"""
from assistant_rag import safety
from assistant_rag.categories import FoodCategories
from assistant_rag.eval.answers import body, rule_check
from assistant_rag.eval.evaluate import metrics, rank_of, threshold_table


def test_rank_and_metrics():
    assert rank_of(["a", "b", "c"], {"c", "z"}) == 3 and rank_of(["a"], {"b"}) is None
    m = metrics([1, 2, None, 1])
    assert m == {"n": 4, "hit1": 0.5, "hit5": 0.75, "mrr": round((1 + 0.5 + 1) / 4, 4)}


def row(id_, category, expected, score, question="問題"):
    return {"id": id_, "category": category, "expected": expected, "question": question,
            "results": {"m": {"top_score": score}}}


def test_threshold_picks_midpoint_and_reports_errors_and_category_rescue():
    rows = [row("P1", "diet", "answer", 0.9), row("P2", "diet", "answer", 0.7),
            row("P3", "diet", "answer", 0.2, "蠔油可以常吃嗎"),
            row("N1", "irrelevant", "refuse", 0.1), row("N2", "non_diet_health", "refuse", 0.3),
            row("X", "medical", "declined_medical", 0.99)]  # 規則題不參與門檻
    cats = FoodCategories({"categories": {"sauce": {"label": "醬料", "query": "q"}}, "foods": {"蠔油": "sauce"}})
    t = threshold_table(rows, "m", cats, {"sauce": 0.95})
    assert t["positives"] == 3 and t["negatives"] == 2 and t["best_correct"] == 4
    assert t["threshold"] == 0.5 and t["interval"] == [0.3, 0.7]
    assert t["positives_below"] == ["P3"] and t["negatives_above"] == []
    assert t["positives_rescued_by_category"] == ["P3"] and t["gap"] == round(0.2 - 0.3, 4)


def test_body_strips_notice_and_tail_and_rule_check_flags():
    text = ("目前沒有「蠔油」本身的資料，以下是「醬料」這一類的一般資訊：\n醬料多含鈉[1]。每天吃3克就好[2]。"
            "你可能得了高血壓病。\n\n" + safety.QUANTITY_NOTE + safety.DISCLAIMER)
    assert body(text) == "醬料多含鈉[1]。每天吃3克就好[2]。你可能得了高血壓病。"
    out = {"type": "category_answer", "text": text, "references": [{"n": 1}]}
    kinds = [k for k, _ in rule_check(out)["issues"]]
    assert "dose" in kinds and "diagnosis" in kinds and "citation" in kinds
    alias = {"type": "answer", "text": "「金瓜」和「南瓜」是同一種食物，以下以「南瓜」的資料回答：\n南瓜含胡蘿蔔素[1]。",
             "references": [{"n": 1}]}
    assert body(alias["text"]) == "南瓜含胡蘿蔔素[1]。" and rule_check(alias)["issues"] == []
    assert rule_check({"type": "emergency", "text": "請盡快就醫", "references": []})["issues"] == [["emergency_without_119", ""]]


def test_category_eval_sampling_and_checks():
    from assistant_rag.categories import FoodCategories
    from assistant_rag.eval import categories as ce
    cats = FoodCategories({"categories": {"veg": {"label": "蔬菜", "query": "q1"}, "oil": {"label": "油", "query": "q2"}},
                           "foods": {"金瓜": "veg", "冬瓜": "veg", "菜心": "veg", "香油": "oil"},
                           "aliases": {"香油": ["麻油"], "菜心": ["芥菜心"]}})
    items = ce.sample(cats, {"冬瓜": 9, "菜心": 5, "金瓜": 1}, per_category=2)
    assert [(i["food"], i["reason"]) for i in items] == [("香油", "category"), ("冬瓜", "category"), ("菜心", "category"), ]
    assert items[0]["question"] == "香油有什麼營養？吃了有什麼好處"
    cat = {"type": "category_answer", "done": {"alias": None},
           "text": "目前沒有「金瓜」本身的資料，以下是「蔬菜」這一類的一般資訊：\n這類食物富含纖維[1]。金瓜很甜[1]。\n\n"
                   + ce.body.__globals__["safety"].DISCLAIMER}
    got = ce.check({"food": "金瓜"}, cat)
    assert got["notice_ok"] is True and got["food_named"] == ["金瓜很甜[1]。"]
    ali = {"type": "answer", "done": {"alias": {"food": "香油", "alias": "麻油"}},
           "text": "「香油」和「麻油」是同一種食物，以下以「麻油」的資料回答：\n麻油有香氣[1]。"}
    assert ce.check({"food": "香油"}, ali) == {"notice_ok": True, "food_named": [], "alias": "麻油"}
