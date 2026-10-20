"""T15：R09 採用資格判定（純函式，不連資料庫）。

輸入為 T14 generation_results.payload 的形狀（calculation＝T12、cost＝T13），
期望值直接取自 spec R09 決策表。
"""
import unittest

from app.services.meal_validation import evaluate


def payload(*, calc_reasons=(), budget_status="OK", missing=(), cost_status="computed", hard=True,
            request_version=1):
    lines = [{"food_id": f, "price_status": "unknown", "missing_reason": "NO_PRICE_DATA"} for f in missing]
    lines.append({"food_id": "FOOD000867", "price_status": "known", "missing_reason": None})
    return {
        "request": {"meal_request_id": "m1", "request_version": request_version},
        "validation": {"hard_constraints_passed": hard},
        "calculation": {"adoptable": not calc_reasons, "block_reasons": [{"code": c} for c in calc_reasons]},
        "cost": ({"status": cost_status, "budget_status": budget_status, "known_cost_total": "40",
                  "lines": lines} if cost_status == "computed"
                 else {"status": cost_status, "reason": "NO_FINAL_REQUIREMENTS", "budget_status": None}),
    }


def codes(result):
    return [b["code"] for b in result["blocks"]]


def kinds(result):
    return [d["kind"] for d in result["pending_decisions"]]


class EligibilityTests(unittest.TestCase):
    def test_complete_within_budget_and_nutrition_is_adoptable(self):
        result = evaluate(payload(), decisions=[], current_request_version=1)
        self.assertTrue(result["adoptable"])
        self.assertEqual((codes(result), kinds(result)), ([], []))

    def test_missing_price_needs_explicit_acceptance_then_is_adoptable_and_flagged(self):
        result = evaluate(payload(budget_status="PRICE_INCOMPLETE", missing=["FOOD001016"]), decisions=[],
                          current_request_version=1)
        self.assertFalse(result["adoptable"])
        self.assertEqual(codes(result), ["PRICE_INCOMPLETE"])
        [pending] = result["pending_decisions"]
        self.assertEqual((pending["kind"], pending["missing_food_ids"]), ("accept_missing_price", ["FOOD001016"]))

        accepted = evaluate(payload(budget_status="PRICE_INCOMPLETE", missing=["FOOD001016"]),
                            decisions=[{"kind": "accept_missing_price", "missing_food_ids": ["FOOD001016"]}],
                            current_request_version=1)
        self.assertTrue(accepted["adoptable"])
        self.assertTrue(accepted["price_exception_accepted"])
        self.assertEqual((codes(accepted), kinds(accepted)), ([], []))

    def test_acceptance_for_a_different_missing_set_does_not_count(self):
        result = evaluate(payload(budget_status="PRICE_INCOMPLETE", missing=["FOOD001016", "FOOD001566"]),
                          decisions=[{"kind": "accept_missing_price", "missing_food_ids": ["FOOD001016"]}],
                          current_request_version=1)
        self.assertFalse(result["adoptable"])
        self.assertEqual(kinds(result), ["accept_missing_price"])

    def test_known_cost_over_budget_is_never_adoptable_and_offers_no_exception(self):
        result = evaluate(payload(budget_status="BUDGET_EXCEEDED", missing=["FOOD001016"]),
                          decisions=[{"kind": "accept_missing_price", "missing_food_ids": ["FOOD001016"]}],
                          current_request_version=1)
        self.assertFalse(result["adoptable"])
        self.assertEqual((codes(result), kinds(result)), (["BUDGET_EXCEEDED"], []))
        self.assertFalse(result["price_exception_accepted"])

    def test_missing_required_nutrition_is_viewable_but_not_adoptable_even_after_continue(self):
        p = payload(calc_reasons=["NUTRITION_INCOMPLETE"])
        result = evaluate(p, decisions=[], current_request_version=1)
        self.assertFalse(result["adoptable"])
        self.assertTrue(result["viewable"])
        self.assertEqual((codes(result), kinds(result)), (["NUTRITION_INCOMPLETE"], ["continue_missing_nutrition"]))
        after = evaluate(p, decisions=[{"kind": "continue_missing_nutrition"}], current_request_version=1)
        self.assertFalse(after["adoptable"])
        self.assertEqual((codes(after), kinds(after)), (["NUTRITION_INCOMPLETE"], []))

    def test_out_of_range_and_unresolved_block_without_any_decision(self):
        result = evaluate(payload(calc_reasons=["NUTRITION_OUT_OF_RANGE", "NO_FEASIBLE_BASELINE"]), decisions=[],
                          current_request_version=1)
        self.assertFalse(result["adoptable"])
        self.assertEqual(kinds(result), [])
        self.assertEqual(codes(result), ["NUTRITION_OUT_OF_RANGE", "NO_FEASIBLE_BASELINE"])

    def test_price_acceptance_does_not_relax_nutrition_range(self):
        result = evaluate(payload(calc_reasons=["NUTRITION_OUT_OF_RANGE"], budget_status="PRICE_INCOMPLETE",
                                  missing=["FOOD001016"]),
                          decisions=[{"kind": "accept_missing_price", "missing_food_ids": ["FOOD001016"]}],
                          current_request_version=1)
        self.assertFalse(result["adoptable"])
        self.assertEqual(codes(result), ["NUTRITION_OUT_OF_RANGE"])

    def test_missing_price_is_not_asked_when_other_conditions_already_fail(self):
        # R09：缺價例外只在「必要營養與其他條件通過」時才詢問；否則接受也無法採用
        result = evaluate(payload(calc_reasons=["NUTRITION_OUT_OF_RANGE"], budget_status="PRICE_INCOMPLETE",
                                  missing=["FOOD001016"]), decisions=[], current_request_version=1)
        self.assertEqual(kinds(result), [])
        self.assertEqual(codes(result), ["NUTRITION_OUT_OF_RANGE", "PRICE_INCOMPLETE"])

    def test_hard_constraint_failure_blocks(self):
        result = evaluate(payload(hard=False), decisions=[], current_request_version=1)
        self.assertFalse(result["adoptable"])
        self.assertEqual(codes(result), ["DIETARY_CONFLICT"])

    def test_cost_not_computed_is_price_incomplete_without_exception(self):
        result = evaluate(payload(calc_reasons=["NUTRITION_INCOMPLETE"], cost_status="not_computed"), decisions=[],
                          current_request_version=1)
        self.assertIn("PRICE_INCOMPLETE", codes(result))
        self.assertNotIn("accept_missing_price", kinds(result))

    def test_result_from_an_older_questionnaire_version_is_not_adoptable(self):
        result = evaluate(payload(request_version=1), decisions=[], current_request_version=2)
        self.assertFalse(result["adoptable"])
        self.assertEqual(codes(result), ["VERSION_CONFLICT"])

    def test_every_block_has_a_readable_message(self):
        result = evaluate(payload(calc_reasons=["NUTRITION_INCOMPLETE"], budget_status="BUDGET_EXCEEDED"),
                          decisions=[], current_request_version=1)
        self.assertTrue(all(b["message"] for b in result["blocks"]))


if __name__ == "__main__":
    unittest.main()
