"""食譜步驟的食材分組代稱展開（決策 #41）。

原網站作者常把食材分組寫在 recipe_ingredients.raw_text 開頭（「[調味]沙茶醬 1匙」「[醃料]米酒」），
步驟再以代稱指涉整組（「先把[調味]在碗事先調好」）。顯示時把代稱展開成「調味（沙茶醬、醬油、糖or味琳）」：

- 只做展開標註，不改寫原文其他字句，不用 LLM；原文另存不動（payload 同時帶原文與顯示文字）。
- 分組只在同一食譜內有效；食材名稱取 raw_text 去掉分組標記與最後的數量（保留原文寫法，如「糖or味琳」）。
- 步驟中的 [X]／【X】：X 是該食譜的分組才展開，否則原樣保留。
- 沒有括號的「調味料」「醃料」「配料」等字樣：該食譜有對應分組（[調味料] 或 [調味]）才展開；
  「調味」單獨出現多為動詞用法（「調味上桌」），不展開。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_LEADING_MARKER = re.compile(r"^\s*(?:\[([^\]]*)\]|【([^】]*)】|[(（]([^)）]*)[)）])\s*")
_BRACKET_REF = r"\[([^\[\]]+)\]|【([^【】]+)】"
# 可不加括號直接展開的分組（調味、醃料、配料、醬汁／醬料／沾醬類）
_BARE_ELIGIBLE = re.compile(r"調味|醃|配料|醬")
# 1027 版工作簿的步驟原文自帶「步驟一：」；畫面以有序清單編號，顯示時去掉避免重複
_STEP_PREFIX = re.compile(r"^\s*步驟\s*[0-9一二三四五六七八九十百零〇]+\s*[：:、.．]\s*")


def _normalize(label: str) -> str:
    return re.sub(r"[\s\[\]【】]", "", label)


def ingredient_group(raw_text: str | None) -> tuple[str | None, str | None]:
    """raw_text → (分組名稱或 None, 去掉分組標記與數量的原文食材名稱)。"""
    if raw_text is None:
        return None, None
    text = raw_text.strip()
    group = None
    match = _LEADING_MARKER.match(text)
    if match:
        group = _normalize(next(g for g in match.groups() if g is not None)) or None
        text = text[match.end():].strip()
    tokens = text.split()
    name = " ".join(tokens[:-1]) if len(tokens) > 1 else text
    return group, name or None


def recipe_groups(raw_texts: Iterable[str | None]) -> dict[str, list[str]]:
    """同一食譜的分組 → 食材名稱（依原順序、去重）。"""
    groups: dict[str, list[str]] = {}
    for raw in raw_texts:
        group, name = ingredient_group(raw)
        if group and name and name not in groups.setdefault(group, []):
            groups[group].append(name)
    return groups


def _bare_tokens(groups: dict[str, list[str]]) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for g in groups:
        if not _BARE_ELIGIBLE.search(g):
            continue
        if re.search(r"料[0-9A-Za-z]*$", g) or g.endswith("醬汁"):
            tokens.setdefault(g, g)
        if not g.endswith("料"):
            tokens.setdefault(f"{g}料", g)
    return tokens


def expand_steps(steps: Iterable[str], raw_texts: Iterable[str | None]) -> list[str]:
    """回傳展開後的顯示文字（新串列；不修改傳入的原文）。沒有分組的食譜只去掉「步驟N：」前綴。"""
    steps = [_STEP_PREFIX.sub("", step) for step in steps]
    groups = recipe_groups(raw_texts)
    if not groups:
        return steps
    tokens = _bare_tokens(groups)
    alternatives = [_BRACKET_REF] + [re.escape(t) for t in sorted(tokens, key=len, reverse=True)]
    pattern = re.compile("|".join(alternatives))

    def expand(step: str) -> str:
        seen: set[str] = set()  # 同一步驟每個分組只展開第一次，避免重複冗長

        def replace(match: re.Match) -> str:
            bracketed = match.group(1) or match.group(2)
            if bracketed is not None:
                label = written = _normalize(bracketed)
                if label not in groups:
                    return match.group(0)
            else:
                written = match.group(0)
                label = tokens[written]
                if match.string[match.end():match.end() + 1] in ("（", "("):
                    return written  # 原文已自帶說明
            if label in seen:
                return written
            seen.add(label)
            return f"{written}（{'、'.join(groups[label])}）"

        return pattern.sub(replace, step)

    return [expand(step) if isinstance(step, str) else step for step in steps]
