import json
import hashlib
from pathlib import Path


BASE_DIR = Path(__file__).parent

YIELD_PATH = (
    BASE_DIR
    / "data"
    / "cooking_yields.json"
)

MAPPING_PATH = (
    BASE_DIR
    / "data"
    / "usda_food_mapping.json"
)


# =========================
# 讀取 USDA Cooking Yield
# =========================

with open(
    YIELD_PATH,
    "r",
    encoding="utf-8"
) as f:
    cooking_yields = json.load(f)


# =========================
# 讀取中英文食材 mapping
# =========================

with open(
    MAPPING_PATH,
    "r",
    encoding="utf-8"
) as f:
    food_mapping = json.load(f)


# =========================
# 中文料理方式 → USDA 英文
# =========================

METHOD_MAP = {
    "烤": "roast",
    "烘烤": "roast",
    "燉": "brais",
    "炙烤": "broil",
    "烤肉": "grill",
    "煎": "pan",
    "炒": "stir",
    "油炸": "deep",
    "微波": "microwave"
}

METHOD_ALIASES = {
    "烤": "roast", "烘烤": "roast", "roasted": "roast", "roast": "roast",
    "水煮": "boil", "煮": "boil", "boiled": "boil", "boil": "boil",
    "燉": "braise", "燉煮": "braise", "braised": "braise", "braise": "braise",
    "蒸": "steam", "蒸煮": "steam", "steam": "steam", "steamed": "steam",
    "煎": "pan", "炒": "stir", "油炸": "deep", "炙烤": "broil",
    "烤肉": "grill", "微波": "microwave", "直接食用": "ready_to_eat"
}


def normalize_method(method):
    text = str(method or "").strip().lower()
    return METHOD_ALIASES.get(text, text)


def get_yield_references(ingredient_name, cooking_method, limit=5):
    """Retrieve related species/cut records, labelled as references, not exact matches.

    Retrieval requires at least the first two curated food keywords (species and
    cut). A skin/form mismatch is allowed only as a labelled LLM analogy.
    Unknown ingredients never receive unrelated meat-and-poultry references.
    """
    mapping = food_mapping.get(ingredient_name, {})
    keywords = mapping.get("usda_keywords", [])
    if len(keywords) < 2:
        return []
    method = normalize_method(cooking_method)
    candidates = []
    for record in cooking_yields:
        description = record["yield_description"].lower()
        if not all(k.lower() in description for k in keywords[:2]):
            continue
        method_match = method in record["preparation_method"].lower()
        exact_form = (bool(mapping.get('food_form')) and all(k.lower() in description for k in keywords)
                      and not any(k.lower() in description for k in mapping.get("exclude_keywords", [])))
        canonical = json.dumps(record, sort_keys=True, ensure_ascii=False)
        ref = {**record, "reference_id": "usda-yield-" + hashlib.sha256(canonical.encode()).hexdigest()[:16],
               "match_type": "exact" if method_match and exact_form else "analogy",
               "method_matches": method_match, "form_matches": exact_form}
        candidates.append((not method_match, not exact_form, ref))
    candidates.sort(key=lambda item: item[:2])
    return [item[2] for item in candidates[:limit]]


# =========================
# 查 USDA Cooking Yield
# =========================

def find_cooking_yield(
    food_keywords,
    cooking_method
):

    method_keyword = METHOD_MAP.get(
        cooking_method,
        cooking_method.lower()
    )

    candidates = []

    for record in cooking_yields:

        description = record[
            "yield_description"
        ].lower()

        method = record[
            "preparation_method"
        ].lower()

        # 食材關鍵字全部都要出現
        food_match = all(
            keyword.lower() in description
            for keyword in food_keywords
        )

        # 烹調方式符合
        method_match = (
            method_keyword in method
        )

        if food_match and method_match:
            candidates.append(record)

    if len(candidates) == 0:
        return {
            "success": False,
            "error": "cooking_yield_not_found"
        }

    if len(candidates) > 1:
        return {
            "success": False,
            "error": "multiple_yield_candidates",
            "candidates": candidates
        }

    return {
        "success": True,
        **candidates[0]
    }


# =========================
# 中文食材 → USDA Yield
# =========================

def find_cooking_yield_by_ingredient(
    ingredient_name,
    cooking_method
):

    mapping = food_mapping.get(
        ingredient_name
    )

    if mapping is None:
        return {
            "success": False,
            "error": "usda_mapping_not_found",
            "ingredient": ingredient_name
        }

    food_keywords = mapping[
        "usda_keywords"
    ]

    return find_cooking_yield(
        food_keywords=food_keywords,
        cooking_method=cooking_method
    )


# =========================
# 生重 → 熟重
# =========================

def calculate_cooked_weight(
    raw_weight_g,
    cooking_yield_pct
):

    return round(
        raw_weight_g
        * cooking_yield_pct
        / 100,
        2
    )
