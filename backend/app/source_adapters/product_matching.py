"""Five-stage matching and deterministic representative-product selection."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .price_models import (
    Availability,
    ConfidenceLevel,
    IngredientQuery,
    MatchDecision,
    ProductCandidate,
    SelectionTier,
    WeightReliability,
)


POLICY_VERSION = "pxmart-selection-v3"

GENERIC_ALIASES = {"蔬菜", "肉類", "海鮮", "澱粉", "香料", "調味料", "食材"}
CURATED_ALIASES: dict[str, tuple[str, ...]] = {
    "青蔥": ("珠蔥", "香蔥"),
    "雞蛋": ("鮮蛋", "洗選蛋"),
    "太白粉": ("馬鈴薯澱粉", "片栗粉"),
    "番茄": ("蕃茄", "西紅柿"),
    "馬鈴薯": ("洋芋",),
    "花椰菜": ("菜花",),
    "高麗菜": ("甘藍",),
    "玉米筍": ("玉米心",),
    "豬絞肉": ("豬肉餡",),
    "牛絞肉": ("牛肉餡",),
    "蛤蠣": ("蛤蜊",),
    "蛤蜊": ("蛤蠣",),
    "大蒜": ("蒜頭",),
    "蒜頭": ("大蒜",),
    "蔥花": ("葱花",),
}

SEARCH_REFINEMENTS: dict[str, tuple[str, ...]] = {
    "水": ("飲用水", "礦泉水"),
    "胡椒": ("胡椒粒", "黑胡椒粒"),
    "奶油": ("無鹽奶油", "有鹽奶油"),
    "起司": ("天然起司", "起司片"),
    "番茄": ("生鮮番茄", "牛番茄"),
    "大蒜": ("蒜頭",),
    "蛤蠣": ("蛤蜊",),
    "香菇": ("鮮香菇",),
    "芝麻": ("白芝麻粒", "黑芝麻粒"),
}

NON_FOOD_MARKERS = (
    "寵物", "寵物食品", "貓糧", "狗糧", "犬糧", "飼料", "貓罐", "狗罐",
    "貓用", "狗用", "犬用", "毛孩", "洗髮", "洗髮精", "沐浴乳", "肥皂",
    "洗衣", "清潔劑", "清潔液", "洗碗", "護髮", "化妝", "保養", "精華液",
    "面膜", "乳液", "牙膏", "除臭", "洗手乳", "漱口水", "ciao", "cat care",
    "寶路", "葛莉思cat", "沛比兒", "狗食", "貓食", "寵食",
    "偉嘉", "西莎", "希爾思", "whiskas",
)

PROCESSED_MARKERS = (
    "熟食", "調理", "即食", "滷", "炸", "醃", "煙燻", "香腸", "肉鬆",
    "水餃", "罐頭", "沙拉", "果汁", "蔬果汁", "醬", "餅", "米果",
    "布丁", "蛋糕", "蛋捲", "麵", "麵包", "金磚", "湯", "丸", "糕", "飲", "茶",
    "風味", "口味", "脆片", "調味", "燒", "香炙", "乾燥", "粉包",
    "炒飯", "便當", "饅頭", "餐包", "雞煲", "火鍋", "底料", "料理包",
    "爆米花", "薯條", "吐司", "愛玉", "方塊酥", "優酪", "優格", "粥",
    "瓜子", "客家小炒", "涼拌", "泡菜", "豆腐乳", "燉", "花生米",
    "卷", "味噌", "麻婆豆腐", "氣泡水", "蒟蒻", "煲", "泡芙", "軟糖",
    "夾心酥", "蛋黃酥", "派", "包子", "堅果", "蔥酥", "果凍", "qq",
    "捲", "鍋", "鍋底", "餃", "餃子", "美乃滋", "炊飯", "千層", "棉花糖",
    "魚條", "麻糬", "壽司", "油雞", "蒜蓉", "十三香", "卡啦", "川椒", "蒸",
    "蛋白棒", "手撕包", "馬卡龍", "夾餡", "魚揚", "營養素",
)
STATE_MARKERS = ("冷凍", "極凍", "凍乾", "熟", "去骨", "帶骨", "去皮", "帶皮", "重組", "鹽鯖")
BENIGN_FRESH_MARKERS = ("冷藏", "生鮮", "洗選", "產銷履歷", "有機")

SPECIFIC_CONFLICTS: dict[str, tuple[str, ...]] = {
    "雞蛋": ("鴨蛋", "鵪鶉蛋", "皮蛋", "鹹蛋", "茶葉蛋", "鐵蛋", "滷蛋"),
    "青蔥": ("洋蔥", "紅蔥頭", "蔥油", "蔥抓餅", "蔥餅", "蔥燒"),
    "太白粉": ("樹薯粉圓", "珍珠粉圓", "地瓜粉"),
    "水": ("冰角", "冰塊", "冰品", "冰棒", "冰淇淋", "製冰", "冰磚"),
    "麻油": ("麻油雞", "麻油猴頭菇", "麻油辣腐乳"),
    "蛤蠣": ("蛤蠣雞", "蛤蜊雞", "蛤蜊絲瓜"),
    "蛤蜊": ("蛤蠣雞", "蛤蜊雞", "蛤蜊絲瓜"),
    "紅椒": ("紅椒粉",),
    "迷迭香": ("迷迭香薯條",),
    "大蒜": ("大蒜花生", "大蒜條", "蒜頭雞", "蒜頭酥", "蒜頭粉"),
    "小卷": ("海苔小卷",),
    "花椒": ("豬", "牛", "雞", "魚", "蝦", "翅", "腿"),
    "胡椒": ("毛豆", "豬", "牛", "雞", "魚", "蝦", "胡椒餅", "胡椒鹽", "肉排"),
    "番茄": ("番茄罐", "番茄冬粉", "番茄汁", "蕃茄汁", "柳葉魚"),
    "奶油": ("鮮奶油", "奶油球", "奶油餐包", "人造奶油", "乳瑪琳"),
    "啤酒": ("啤酒酵母",),
    "蛋黃": ("蛋黃香", "蛋黃素"),
    "鹹蛋黃": ("蛋黃酥", "方塊酥"),
    "鹹蛋": ("蛋黃酥", "方塊酥"),
    "高粱酒": ("高粱酒豬", "高粱酒豆腐乳"),
    "可樂": ("可樂果",),
    "黑醋": ("黑醋栗",),
    "玉米": ("玉米筍", "玉米粉"),
    "地瓜": ("地瓜葉",),
    "白蘿蔔": ("紅蘿蔔",),
    "紅蘿蔔": ("白蘿蔔",),
    "芹菜": ("西洋芹",),
    "雞胸肉": ("雞腿", "雞翅"),
    "雞腿": ("雞胸", "雞翅"),
    "雞翅": ("雞胸", "雞腿", "花椒", "椒鹽", "三杯", "卡啦", "香草", "吮指"),
    "米粉": ("在來米粉", "蓬萊米粉", "糯米粉"),
    "芝麻": ("芝麻粉", "芝麻包", "芝麻麻糬", "芝麻糊"),
    "黑芝麻": ("黑芝麻粉", "黑芝麻包", "黑芝麻糊", "五麥", "藜麥", "穀奶"),
    "花椰菜": ("青花菜", "青花椰菜", "綠花椰菜", "三重奏"),
    "玉米筍": ("玉米粒", "甜玉米"),
    "甘草": ("甘草芭樂", "甘草喉糖"),
    "蝦米": ("朝天辣椒",),
    "貢丸": ("鴨肉", "牛肉", "魚肉"),
    "牛腩": ("黑胡椒牛腩",),
    "香葉": ("迷迭香",),
    "鮭魚": ("魚頭", "魚骨"),
    "鯛魚": ("魚下巴", "鯛魚下巴"),
    "鯖魚": ("鹽鯖", "茄汁", "易開罐"),
    "鱈魚": ("鱈魚香絲", "鱈魚起司", "三明治", "雙珍", "比活力", "芝麻鱈魚"),
    "檸檬": ("檸檬薄片", "福義軒", "冰磚", "冰角", "原汁", "檸檬味", "酸糖", "酸酸李"),
    "毛豆": ("黑胡椒毛豆", "調味毛豆"),
    "巧克力": ("熱巧克力", "繽紛樂"),
    "蛋白": ("蛋白牛奶", "奶蛋白", "蛋白飲", "蛋白粉", "蛋白棒", "蛋白杯", "高蛋白", "優蛋白", "乳清", "補體素", "燕麥"),
    "草莓": ("咖啡", "拿鐵", "果醬", "優格", "優酪", "威化", "百奇", "pocky", "kitkat", "草莓棒", "甜心草莓"),
    "蒜頭": ("蒜頭雞", "蒜頭酥", "蒜頭粉", "大蒜條"),
    "沙茶": ("蜜汁", "豆干", "肉乾"),
    "椰菜": ("青花椰菜", "花椰菜"),
    "蘿蔔": ("香辣蘿蔔", "醃蘿蔔", "蘿蔔乾"),
    "豆豉": ("朝天小魚",),
}

AMBIGUOUS_VARIANTS: dict[str, tuple[str, ...]] = {
    "蘿蔔": ("紅蘿蔔", "白蘿蔔", "胡蘿蔔"),
    "麵粉": ("高筋", "中筋", "低筋"),
    "肉絲": ("牛", "豬", "雞", "前腿", "里肌"),
    "高湯": ("雞", "豬", "牛", "魚", "昆布", "海鮮", "蟹"),
    "沙茶": ("沙茶醬", "沙茶粉"),
    "肉桂": ("肉桂粉", "肉桂棒"),
    "優格": ("橘", "水蜜桃", "草莓", "藍莓", "水果"),
}

SHORT_NAME_IDENTITY_TERMS: dict[str, tuple[str, ...]] = {
    "水": ("飲用水", "純水", "礦泉水", "包裝水", "蒸餾水", "RO水"),
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[\s\-_()（）/]+", "", value)


def build_search_terms(query: IngredientQuery, max_terms: int = 3) -> list[str]:
    """Build a bounded, conservative canonical/alias search sequence."""

    terms: list[str] = []
    for term in (
        query.canonical_name,
        *CURATED_ALIASES.get(query.canonical_name, ()),
        *SEARCH_REFINEMENTS.get(query.canonical_name, ()),
        *query.aliases,
    ):
        cleaned = term.strip()
        if (
            cleaned
            and len(normalize(cleaned)) >= 2
            and normalize(cleaned) not in {normalize(value) for value in GENERIC_ALIASES}
            and cleaned not in terms
        ):
            terms.append(cleaned)
        elif cleaned == query.canonical_name and cleaned not in terms:
            terms.append(cleaned)
        if len(terms) >= max_terms:
            break
    return terms


def classify_ingredient(canonical_name: str) -> str:
    name = normalize(canonical_name)
    if name in {"水", "飲用水", "礦泉水"}:
        return "water"
    if any(term in name for term in ("蜂蜜", "高湯", "雞湯")):
        return "liquid_food"
    if "蛋" in name:
        return "egg"
    if any(term in name for term in ("雞", "豬", "牛", "羊", "肉", "排骨")):
        return "meat"
    if any(term in name for term in ("魚", "蝦", "蛤", "蚵", "貝", "花枝", "透抽")):
        return "seafood"
    if any(term in name for term in ("奶", "乳", "起司", "優格")):
        return "dairy"
    if any(term in name for term in ("油", "醋", "醬油", "酒", "汁")):
        return "liquid_seasoning"
    if any(term in name for term in ("粉", "米", "麵", "豆", "麥", "糖", "鹽")):
        return "dry_grocery"
    return "produce"


@dataclass(frozen=True)
class PackageInfo:
    total_quantity: Decimal | None = None
    unit: str | None = None
    description: str | None = None
    item_count: int | None = None
    per_item_quantity: Decimal | None = None
    tolerance: Decimal | None = None
    edible_quantity: Decimal | None = None
    weight_basis: str | None = None
    reliability: WeightReliability = WeightReliability.UNKNOWN


def _quantity(amount: str, unit: str) -> tuple[Decimal, str]:
    value = Decimal(amount)
    lowered = unit.lower()
    if lowered in {"kg", "公斤"}:
        return value * Decimal("1000"), "g"
    if lowered in {"l", "公升"}:
        return value * Decimal("1000"), "ml"
    if lowered in {"公克", "克"}:
        return value, "g"
    if lowered == "毫升":
        return value, "ml"
    return value, lowered


def parse_package_details(text: str) -> PackageInfo:
    """Parse total, count, tolerance and explicit edible-weight information."""

    normalized = unicodedata.normalize("NFKC", text)
    unit = r"kg|公斤|g|公克|克|ml|毫升|l|公升"

    edible_match = re.search(
        rf"(?:可食部|固形量)\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>{unit})",
        normalized,
        re.IGNORECASE,
    )
    edible_quantity = None
    if edible_match:
        edible_quantity, _ = _quantity(
            edible_match.group("amount"), edible_match.group("unit")
        )
    weight_basis = (
        "gross_with_edible"
        if edible_quantity is not None and "毛重" in normalized
        else "edible"
        if edible_quantity is not None
        else "gross"
        if "毛重" in normalized
        else "net"
    )

    tolerance_match = re.search(
        rf"(?<![\d.])(?P<amount>\d+(?:\.\d+)?)\s*"
        rf"(?:±\s*(?P<tol_before>\d+(?:\.\d+)?)\s*(?P<unit_before>{unit})|"
        rf"(?P<unit_after>{unit})\s*±\s*(?P<tol_after>\d+(?:\.\d+)?)\s*(?:{unit})?)",
        normalized,
        re.IGNORECASE,
    )
    if tolerance_match:
        chosen_unit = tolerance_match.group("unit_before") or tolerance_match.group("unit_after")
        amount, canonical_unit = _quantity(tolerance_match.group("amount"), chosen_unit)
        tolerance, tolerance_unit = _quantity(
            tolerance_match.group("tol_before") or tolerance_match.group("tol_after"),
            chosen_unit,
        )
        if tolerance_unit != canonical_unit:
            return PackageInfo(description=tolerance_match.group(0))
        count_match = re.search(r"[（(]\s*(\d+)\s*(?:入|顆|粒|枚)\s*[）)]", normalized)
        item_count = int(count_match.group(1)) if count_match else None
        per_item = amount / item_count if item_count else None
        return PackageInfo(
            total_quantity=amount,
            unit=canonical_unit,
            description=tolerance_match.group(0),
            item_count=item_count,
            per_item_quantity=per_item,
            tolerance=tolerance,
            edible_quantity=edible_quantity,
            weight_basis=weight_basis,
            reliability=(
                WeightReliability.UNKNOWN
                if weight_basis == "gross"
                else WeightReliability.NOMINAL_TOLERANCE
            ),
        )

    lower_bound_match = re.search(
        rf"(?<![\d.])(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>{unit})\s*(?:以上|起)",
        normalized,
        re.IGNORECASE,
    )
    if lower_bound_match:
        amount, canonical_unit = _quantity(
            lower_bound_match.group("amount"), lower_bound_match.group("unit")
        )
        return PackageInfo(
            total_quantity=amount,
            unit=canonical_unit,
            description=lower_bound_match.group(0),
            edible_quantity=edible_quantity,
            weight_basis="lower_bound",
            reliability=WeightReliability.LOWER_BOUND,
        )

    multipack_match = re.search(
        rf"(?<![\d.])(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>{unit})\s*"
        r"(?:[xX×*])\s*(?P<count>\d+)\s*(?:入|包|瓶|罐|盒|顆|粒|枚)?",
        normalized,
        re.IGNORECASE,
    )
    if multipack_match:
        per_item, canonical_unit = _quantity(
            multipack_match.group("amount"), multipack_match.group("unit")
        )
        count = int(multipack_match.group("count"))
        return PackageInfo(
            total_quantity=per_item * count,
            unit=canonical_unit,
            description=multipack_match.group(0),
            item_count=count,
            per_item_quantity=per_item,
            edible_quantity=edible_quantity,
            weight_basis=weight_basis,
            reliability=(
                WeightReliability.UNKNOWN
                if weight_basis == "gross"
                else WeightReliability.EXACT
            ),
        )

    simple_match = re.search(
        rf"(?<![\d.])(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>{unit})",
        normalized,
        re.IGNORECASE,
    )
    if not simple_match:
        return PackageInfo(edible_quantity=edible_quantity)
    amount, canonical_unit = _quantity(simple_match.group("amount"), simple_match.group("unit"))
    count_match = re.search(r"[（(]\s*(\d+)\s*(?:入|顆|粒|枚)\s*[）)]", normalized)
    count = int(count_match.group(1)) if count_match else None
    return PackageInfo(
        total_quantity=amount,
        unit=canonical_unit,
        description=simple_match.group(0),
        item_count=count,
        per_item_quantity=(amount / count) if count else None,
        edible_quantity=edible_quantity,
        weight_basis=weight_basis,
        reliability=(
            WeightReliability.UNKNOWN
            if weight_basis == "gross"
            else WeightReliability.EXACT
        ),
    )


def evaluate_match(
    query: IngredientQuery,
    product_name: str,
    package: PackageInfo,
) -> tuple[MatchDecision, str, int, ConfidenceLevel, list[str], list[str]]:
    """Classify identity/state first, then score only candidates that survive."""

    normalized_name = normalize(product_name)
    canonical = normalize(query.canonical_name)
    ingredient_type = classify_ingredient(query.canonical_name)
    reasons: list[str] = [f"ingredient type: {ingredient_type}"]
    rejected: list[str] = []

    non_food = [marker for marker in NON_FOOD_MARKERS if normalize(marker) in normalized_name]
    if non_food:
        return MatchDecision.REJECT, ingredient_type, 0, ConfidenceLevel.LOW, reasons, [
            "non-food product: " + ", ".join(non_food)
        ]

    conflicts = [term for term in SPECIFIC_CONFLICTS.get(query.canonical_name, ()) if normalize(term) in normalized_name]
    if conflicts:
        return MatchDecision.REJECT, ingredient_type, 0, ConfidenceLevel.LOW, reasons, [
            "ingredient-variety conflict: " + ", ".join(conflicts)
        ]

    canonical_match = canonical in normalized_name
    curated = [term for term in CURATED_ALIASES.get(query.canonical_name, ()) if normalize(term) in normalized_name]
    aliases = [
        term for term in query.aliases
        if len(normalize(term)) >= 2
        and normalize(term) not in {normalize(value) for value in GENERIC_ALIASES}
        and normalize(term) in normalized_name
    ]
    if len(canonical) <= 1 and normalized_name != canonical:
        short_identity_terms = [
            term
            for term in SHORT_NAME_IDENTITY_TERMS.get(query.canonical_name, ())
            if normalize(term) in normalized_name
        ]
        if not short_identity_terms and not curated and not aliases:
            return MatchDecision.REJECT, ingredient_type, 0, ConfidenceLevel.LOW, reasons, [
                "short canonical name requires an exact name or a specific safe identity term"
            ]
    if not canonical_match and not curated and not aliases:
        return MatchDecision.REJECT, ingredient_type, 0, ConfidenceLevel.LOW, reasons, [
            "canonical name and safe aliases not present"
        ]

    if canonical_match:
        score = 70
        reasons.append(f"canonical identity matched: {query.canonical_name}")
    elif curated:
        score = 58
        reasons.append(f"curated alias matched: {curated[0]}")
    else:
        score = 50
        reasons.append(f"workbook alias matched: {aliases[0]}")

    processed = [
        marker for marker in PROCESSED_MARKERS
        if marker in product_name and normalize(marker) not in canonical
    ]
    if processed:
        rejected.append("processing-state mismatch: " + ", ".join(processed))
        return MatchDecision.REJECT, ingredient_type, 0, ConfidenceLevel.LOW, reasons, rejected

    state_conflicts = [
        marker for marker in STATE_MARKERS
        if marker in product_name and normalize(marker) not in canonical
    ]
    decision = MatchDecision.MATCH
    if state_conflicts:
        decision = MatchDecision.REVIEW
        reasons.append("state differs from canonical: " + ", ".join(state_conflicts))
        score -= 20
    elif any(marker in product_name for marker in BENIGN_FRESH_MARKERS):
        score += 3
        reasons.append("freshness/traceability marker is compatible")

    ambiguous_variants = [
        marker for marker in AMBIGUOUS_VARIANTS.get(query.canonical_name, ())
        if normalize(marker) in normalized_name
    ]
    if ambiguous_variants:
        decision = MatchDecision.REVIEW
        score -= 15
        reasons.append(
            "canonical name is broader than product variant: "
            + ", ".join(ambiguous_variants)
        )

    if package.unit and ingredient_type not in {
        "liquid_seasoning", "dairy", "water", "liquid_food"
    } and package.unit != "g":
        return MatchDecision.REJECT, ingredient_type, 0, ConfidenceLevel.LOW, reasons, [
            f"weight basis incompatible with solid ingredient: {package.unit}"
        ]
    if package.reliability is WeightReliability.EXACT:
        score += 12
        reasons.append("exact package net quantity parsed")
    elif package.reliability is WeightReliability.NOMINAL_TOLERANCE:
        score += 10
        reasons.append("nominal package quantity with stated tolerance parsed")
    elif package.reliability is WeightReliability.LOWER_BOUND:
        decision = MatchDecision.REVIEW
        reasons.append("package quantity is a lower bound")
    else:
        decision = MatchDecision.REVIEW
        reasons.append("package quantity is unavailable")

    confidence = ConfidenceLevel.HIGH if score >= 80 and decision is MatchDecision.MATCH else (
        ConfidenceLevel.MEDIUM if score >= 55 else ConfidenceLevel.LOW
    )
    return decision, ingredient_type, score, confidence, reasons, rejected


@dataclass(frozen=True)
class SelectionOutcome:
    tier: SelectionTier
    selected: ProductCandidate | None
    reasons: list[str]
    method: str


def _unit_price(candidate: ProductCandidate) -> Decimal:
    denominator = candidate.edible_quantity or candidate.package_total_quantity
    if candidate.price is None or not denominator:
        return Decimal("Infinity")
    return candidate.price / denominator


def select_candidate(
    candidates: list[ProductCandidate],
    *,
    preferred_product_id: str | None = None,
    now: datetime | None = None,
) -> SelectionOutcome:
    """Apply hard gates before deterministic representative-product ranking."""

    now = now or datetime.now(timezone.utc)

    def is_current_priced(candidate: ProductCandidate) -> bool:
        return (
            candidate.match_decision is not MatchDecision.REJECT
            and candidate.availability is Availability.IN_STOCK
            and candidate.price is not None
            and candidate.price > 0
            and (
                candidate.valid_until is None
                or candidate.valid_until >= now
            )
        )

    current = [
        candidate for candidate in candidates if is_current_priced(candidate)
    ]
    high = [
        candidate for candidate in current
        if candidate.confidence is ConfidenceLevel.HIGH
        and candidate.match_decision is MatchDecision.MATCH
        and candidate.weight_reliability in {
            WeightReliability.EXACT,
            WeightReliability.NOMINAL_TOLERANCE,
        }
        and candidate.package_total_quantity is not None
        and candidate.package_unit is not None
    ]
    if preferred_product_id:
        preferred = next(
            (candidate for candidate in high if candidate.product_id == preferred_product_id),
            None,
        )
        if preferred:
            return SelectionOutcome(
                SelectionTier.AUTO_ADOPTED,
                preferred,
                ["existing representative product remains valid", f"policy {POLICY_VERSION}"],
                "representative_refresh",
            )

    if high:
        reliability_rank = {
            WeightReliability.EXACT: 0,
            WeightReliability.NOMINAL_TOLERANCE: 1,
        }
        selected = sorted(
            high,
            key=lambda candidate: (
                -candidate.score,
                reliability_rank[candidate.weight_reliability],
                _unit_price(candidate),
                candidate.product_id or candidate.product_name,
            ),
        )[0]
        return SelectionOutcome(
            SelectionTier.AUTO_ADOPTED,
            selected,
            [
                "passed ingredient identity, state, availability, price and weight gates",
                "ranked by semantic score, weight reliability, then unit price",
                f"selected score {selected.score}; policy {POLICY_VERSION}",
            ],
            "scored_search",
        )

    estimates = [
        candidate for candidate in current
        if candidate.confidence in {ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH}
        and candidate.package_total_quantity is not None
        and candidate.package_unit is not None
    ]
    if estimates:
        selected = sorted(
            estimates,
            key=lambda candidate: (-candidate.score, _unit_price(candidate), candidate.product_id or candidate.product_name),
        )[0]
        return SelectionOutcome(
            SelectionTier.ESTIMATE,
            selected,
            [
                "candidate is usable only as an estimate because an automatic-adoption gate failed",
                f"selected score {selected.score}; policy {POLICY_VERSION}",
            ],
            "estimated_alternative",
        )

    return SelectionOutcome(
        SelectionTier.MISSING,
        None,
        ["no current candidate passed the minimum identity/price/package gates"],
        "no_selection",
    )
