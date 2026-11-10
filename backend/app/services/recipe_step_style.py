"""Read-only editorial checks for conversational, ordered recipe instructions."""

from collections.abc import Iterable
import re
import unicodedata


STYLE_VERSION = "home-cooking-steps-v1"
EDITOR_NOTE = "本版備料以食材表採值為準。"
NUMERALS = "零〇一二三四五六七八九十百"
STEP_PREFIX = re.compile(rf"^\s*步驟\s*([0-9０-９{NUMERALS}]+)\s*[：:、.．]\s*")
POT = r"(?:湯鍋|炒鍋|平底鍋|深鍋|不沾鍋|鍋)"
POT_CODE = re.compile(
    rf"(?<![A-Za-z0-9])(?:[A-Za-z]|[甲乙丙丁戊己庚辛壬癸])\s*"
    rf"(?:號|[)】.、:])?\s*{POT}"
    rf"|鍋(?:子|具)?\s*[:=]?\s*[(【]?\s*[A-Za-z](?![A-Za-z0-9])"
    rf"|(?:第\s*)?[0-9{NUMERALS}]+\s*號\s*{POT}"
    rf"|第\s*[0-9{NUMERALS}]+\s*口?\s*{POT}"
)
REFERENCE = re.compile(
    r"(?:剛才|方才|前面|上一步)(?:的|用過的)?\s*(?:那[個口只支]?|這[個口只支]?)?\s*(?:鍋|碗|盤|容器|材料|醬汁)"
    r"|(?:那鍋|上一鍋|前一鍋|原鍋|同上|做法同前|步驟\s*[A-Za-z](?![A-Za-z0-9]))"
)
SUBSTEP = re.compile(r"(?:^|[。；！？\n])\s*(?:\(\d+\)|\d+[、.)](?!\d))")
TRANSITIONS = re.compile(r"(^|[。！？；]\s*)(?:然後|接下來|接下去)[，,、]?\s*")
WORDING = {"加進": "加入", "放進": "放入", "撈出來": "撈起", "把火關掉": "關火"}
WORDING_PATTERN = re.compile("|".join(WORDING))
COOKING_FACTS = re.compile(
    rf"\d+(?:[./–—~～-]\d+)*|[{NUMERALS}]+\s*(?:小時|分鐘|秒|度|公克|克|公斤|毫升|大匙|小匙|杯)"
    r"|(?:中小|中大|微|小|中|大|文|旺)火|關火|熄火"
    r"|(?<![A-Za-z])(?:kg|mg|ml|g)(?![A-Za-z])",
    re.IGNORECASE,
)


def _step_number(label: str) -> int | None:
    label = unicodedata.normalize("NFKC", label)
    if label.isascii() and label.isdecimal():
        return int(label)
    digits = dict(zip("零一二三四五六七八九", range(10)))
    if label in digits:
        return digits[label]
    if label == "十":
        return 10
    if label.count("十") == 1:
        tens, ones = label.split("十")
        if (not tens or tens in digits) and (not ones or ones in digits):
            return digits.get(tens, 1) * 10 + digits.get(ones, 0)
    if label in ("一百", "兩百", "二百"):
        return 100 if label == "一百" else 200
    # Unknown numbering is flagged for review instead of being removed.
    return None


def _check_step(step_no: int, original: str) -> dict | None:
    notes = []
    manual = []
    body = original
    note = ""
    if body.startswith(EDITOR_NOTE):
        note, body = EDITOR_NOTE, body[len(EDITOR_NOTE):]
    prefix = STEP_PREFIX.match(body)
    if prefix:
        if _step_number(prefix[1]) == step_no:
            body = body[prefix.end():]
            notes.append("移除和畫面編號重複的步驟標籤。")
        else:
            manual.append("內文步驟編號和這筆步驟的序號不一致，請核對原本的操作順序。")

    searchable = unicodedata.normalize("NFKC", body)
    if POT_CODE.search(searchable):
        manual.append("含鍋具代號，請按這道食譜的前後文寫出鍋具用途。")
    if REFERENCE.search(searchable):
        manual.append("有指向前文的代稱，請確認所指的鍋具、食材或步驟。")
    if len(SUBSTEP.findall(searchable)) >= 2:
        manual.append("同一筆步驟包含多段編號，請核對是否需要分段及原操作順序。")

    suggested = TRANSITIONS.sub(lambda match: match[1] + "接著", body)
    suggested = WORDING_PATTERN.sub(lambda match: WORDING[match[0]], suggested)
    if suggested != body:
        notes.append("以一致的動作或轉折用詞呈現。")
    if (COOKING_FACTS.findall(body.replace("把火關掉", "關火"))
            != COOKING_FACTS.findall(suggested)):
        manual.append("建議文字的數字、單位或火候與原文有差異，請人工核對。")

    if not notes and not manual:
        return None
    return {
        "step_no": step_no,
        "original": original,
        "suggestion": note + suggested if notes and not manual else None,
        "status": "needs_review" if manual else "suggested",
        "reasons": manual or notes,
    }


def check_recipe_steps(steps: Iterable[tuple[int, str]]) -> dict:
    """Return only rows with suggestions or review flags, without mutating steps."""
    checked_count = 0
    findings = []
    for number, text in steps:
        checked_count += 1
        finding = _check_step(number, text)
        if finding:
            findings.append(finding)
    return {"style_version": STYLE_VERSION, "checked_count": checked_count, "findings": findings}
