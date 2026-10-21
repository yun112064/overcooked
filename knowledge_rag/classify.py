"""營養知識分類結果的程式驗證（決策 #29；純函式，不連網）。

模型（雲端 gpt-5.4-mini）回傳 is_nutrition_knowledge／nutrition_fact／evidence_quote／reason；
寫入知識庫前一律在團隊電腦驗證，驗證失敗視為 invalid（退回並保留原因）：
- evidence_quote 必須是 excerpt 的連續子字串（只容許前後空白差異）；
- nutrition_fact 必須是一句（≤ MAX_FACT_CHARS 字）、含中文、含營養關鍵字，且至少一個關鍵字也出現在 evidence_quote。
"""
import re

from knowledge_rag.healthnews import _norm, load_nutrition_keywords

NUTRITION, NOT_NUTRITION, INVALID = "nutrition", "not_nutrition", "invalid"
MAX_FACT_CHARS = 80
CJK = re.compile(r"[一-鿿]")


def keywords_in(text: str, keywords) -> set[str]:
    text = _norm(text or "")
    return {k for k in keywords if _norm(k) in text}


def validate(record: dict, item: dict | None, keywords=None) -> dict:
    """回傳 {classification, nutrition_fact, evidence_quote, reason}；非 nutrition 時 fact／quote 為 None。"""
    keywords = keywords or load_nutrition_keywords()

    def invalid(code: str) -> dict:
        return {"classification": INVALID, "nutrition_fact": None, "evidence_quote": None,
                "reason": f"{code}：{(item or {}).get('reason') or ''}".rstrip("：")}

    if item is None:
        return invalid("MISSING_OUTPUT")
    reason = (item.get("reason") or "").strip()
    if item.get("is_nutrition_knowledge") is not True:
        return {"classification": NOT_NUTRITION, "nutrition_fact": None, "evidence_quote": None,
                "reason": reason or "非營養／健康效益內容"}
    fact = (item.get("nutrition_fact") or "").strip()
    quote = (item.get("evidence_quote") or "").strip()
    if not fact or len(fact) > MAX_FACT_CHARS or "\n" in fact or not CJK.search(fact):
        return invalid("FACT_NOT_ONE_SENTENCE")
    if not quote or quote not in record["excerpt"]:
        return invalid("EVIDENCE_NOT_IN_EXCERPT")
    fact_keywords = keywords_in(fact, keywords)
    if not fact_keywords:
        return invalid("FACT_WITHOUT_NUTRIENT_OR_BENEFIT")
    if not fact_keywords & keywords_in(quote, keywords):
        return invalid("FACT_NOT_SUPPORTED_BY_EVIDENCE")
    return {"classification": NUTRITION, "nutrition_fact": fact, "evidence_quote": quote,
            "reason": reason or "陳述營養成分或健康效益"}
