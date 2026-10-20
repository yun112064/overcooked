"""T15 ValidationResult：依 spec R09 決策表合併 T12 營養與 T13 成本，判定採用資格（純函式）。

| 情況 | 可採用 | 可做的決定 |
|---|---|---|
| 必要條件完整、成本不超預算、熱量與蛋白質通過 | 是 | — |
| 缺價格（已知成本未超預算，且其他條件都通過才詢問） | 使用者明確接受缺價例外後可 | accept_missing_price |
| 已知成本已超預算（不論是否缺價） | 否 | — |
| 缺必要營養（熱量或蛋白質） | 否（補齊重驗前都不可） | continue_missing_nutrition（僅確認繼續查看） |
| ±10% 不符、基準無解、食材無法驗證 | 否 | — |
| 禁忌、指定菜、昨日未指定菜違規 | 否 | — |
| 問卷已改版 | 否 | — |

決定只針對單一結果版本；接受缺價例外時保存當時的缺價食材集合，集合不同即不算數。
任何決定都不放寬禁忌、指定菜、±10% 或昨日去重。最終採用由 T16 以同一函式重新判定。
"""

ACCEPT_MISSING_PRICE = "accept_missing_price"
CONTINUE_MISSING_NUTRITION = "continue_missing_nutrition"
DECISION_KINDS = (ACCEPT_MISSING_PRICE, CONTINUE_MISSING_NUTRITION)

MESSAGES = {
    "DIETARY_CONFLICT": "菜單未通過過敏／禁吃、指定菜或昨日去重檢查，請修改問卷或重新生成。",
    "INGREDIENT_UNRESOLVED": "部分食材無法換算重量，無法驗證營養；補齊資料前不可採用。",
    "NUTRITION_INCOMPLETE": "缺少熱量或蛋白質的必要營養資料，可查看但不可採用；補齊並重新驗證後才可採用。",
    "NUTRITION_OUT_OF_RANGE": "熱量或蛋白質超出目標 ±10%，可查看差距但不可採用。",
    "NO_FEASIBLE_BASELINE": "無法把基準配方調整到目標範圍內，不可採用。",
    "BUDGET_EXCEEDED": "已知食材成本已超過預算，不可採用（缺價例外也不能放寬）。",
    "PRICE_INCOMPLETE": "部分需補買食材查無價格，預算無法完整驗證；需明確接受缺價例外後才可採用。",
    "VERSION_CONFLICT": "問卷已在此結果產生後修改，請重新生成後再採用。",
}


def _block(code: str, **details) -> dict:
    return {"code": code, "message": MESSAGES.get(code, code), **details}


def missing_price_food_ids(payload: dict) -> list[str]:
    cost = payload.get("cost") or {}
    return sorted({ln["food_id"] for ln in cost.get("lines") or [] if ln.get("price_status") == "unknown"})


def evaluate(payload: dict, *, decisions: list[dict], current_request_version: int | None) -> dict:
    """回傳 ValidationResult：adoptable、viewable、blocks、pending_decisions、price_exception_accepted。"""
    blocks: list[dict] = []
    pending: list[dict] = []
    recorded = {d["kind"]: d for d in decisions}

    if current_request_version is not None and payload["request"]["request_version"] != current_request_version:
        blocks.append(_block("VERSION_CONFLICT", result_request_version=payload["request"]["request_version"],
                             current_request_version=current_request_version))
    if not (payload.get("validation") or {}).get("hard_constraints_passed"):
        blocks.append(_block("DIETARY_CONFLICT"))

    calc_codes = list(dict.fromkeys(r["code"] for r in (payload.get("calculation") or {}).get("block_reasons", [])))
    for code in calc_codes:
        blocks.append(_block(code))
    if "NUTRITION_INCOMPLETE" in calc_codes and CONTINUE_MISSING_NUTRITION not in recorded:
        pending.append({"kind": CONTINUE_MISSING_NUTRITION, "message": "缺少必要營養資料：是否繼續查看此菜單？（仍不可採用）"})

    cost = payload.get("cost") or {}
    accepted = False
    if cost.get("status") != "computed":
        blocks.append(_block("PRICE_INCOMPLETE", reason=cost.get("reason") or "PRICE_NOT_COMPUTED"))
    elif cost.get("budget_status") == "BUDGET_EXCEEDED":
        blocks.append(_block("BUDGET_EXCEEDED", known_cost_total=cost.get("known_cost_total"),
                             budget=cost.get("budget")))
    elif cost.get("budget_status") == "PRICE_INCOMPLETE":
        missing = missing_price_food_ids(payload)
        decision = recorded.get(ACCEPT_MISSING_PRICE)
        if decision is not None and sorted(decision.get("missing_food_ids") or []) == missing:
            accepted = True
        else:
            other_blocks = bool(blocks)
            blocks.append(_block("PRICE_INCOMPLETE", missing_food_ids=missing))
            if other_blocks:
                return _out(blocks, pending, accepted)  # 其他條件已不通過：接受缺價也無法採用，不詢問
            pending.append({"kind": ACCEPT_MISSING_PRICE, "missing_food_ids": missing,
                            "known_cost_total": cost.get("known_cost_total"),
                            "message": "部分食材查無價格：可重試查價，或接受缺價例外繼續（標示預算無法完整驗證）。"})
    return _out(blocks, pending, accepted)


def _out(blocks: list[dict], pending: list[dict], accepted: bool) -> dict:
    return {
        "adoptable": not blocks,
        "viewable": True,
        "blocks": blocks,
        "pending_decisions": pending,
        "price_exception_accepted": accepted,
    }


def request_status(payload: dict, *, decisions: list[dict]) -> str:
    """結果產生後的問卷狀態：仍有待使用者決定的缺值即 awaiting_decision，否則 result_ready（不代表可採用）。"""
    result = evaluate(payload, decisions=decisions, current_request_version=None)
    return "awaiting_decision" if result["pending_decisions"] else "result_ready"
