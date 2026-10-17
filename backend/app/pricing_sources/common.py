from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
USER_AGENT = "Overcook/1.0 educational price checker"
NON_FOOD_WORDS = (
    "收納盒",
    "玩具",
    "寵物",
    "清潔",
    "洗髮",
    "沐浴",
    "保健食品",
    "料理包",
    "即食",
    "餅乾",
    "沙琪瑪",
    "蛋糕",
    "麵包",
)
PROCESSED_STATE_WORDS = (
    "脫水",
    "調理包",
    "水餃",
    "濃湯",
    "湯包",
    "蔥餅",
    "蘇打餅",
    "爆漿",
    "人造",
)
ACCESS_CHALLENGE_WORDS = (
    "captcha",
    "驗證碼",
    "access denied",
    "cf-chl-",
    "cloudflare ray id",
)
UNSAFE_ALIAS_TERMS = {"塩巴", "糊椒", "\u80e1\u6912\u678c"}
HIGH_RISK_PRODUCT_WORDS = {
    "醬油": ("醬油膏", "醬油風味", "調理包"),
    "米酒": ("米酒頭", "甜米酒", "酒釀", "米酒水"),
    "香油": ("辣油", "花椒油", "蔥油", "香蒜油", "調味油"),
    "食鹽": ("低鈉", "減鈉", "胡椒鹽", "調味鹽", "椒鹽", "複方鹽"),
    "大蒜": ("蒜蓉醬", "蒜醬", "蒜粉", "蒜油", "蒜泥", "蒜蓉", "蒜末", "蒜頭酥", "蒜酥", "蒜味", "調味蒜"),
    "薑": ("薑茶", "薑粉", "薑糖", "薑汁", "薑母茶"),
    "辣椒": ("辣椒醬", "辣椒粉", "辣椒油", "調味辣椒"),
    "雞腿肉": ("調味", "熟食", "即食", "炸雞", "烤雞", "煙燻", "雞腿排便當"),
    "香菇": ("乾香菇", "香菇乾", "脫水", "香菇醬", "香菇湯"),
    "奶油": ("人造奶油", "乳酪", "抹醬", "蒜香", "調味奶油"),
    "胡椒粉": ("黑胡椒", "白胡椒", "胡椒鹽", "椒鹽"),
    "豬絞肉": ("調理", "漢堡", "肉丸", "水餃", "香腸", "熟食"),
}


class SourceAccessBlocked(RuntimeError):
    """來源明確拒絕或要求人工驗證；呼叫端必須停止該來源。"""


@dataclass(frozen=True)
class RetailRecord:
    source_record_id: str
    product_name: str
    package_weight_g: Decimal
    package_price: Decimal
    source_url: str
    promotion_text: str | None = None
    region: str | None = "台灣線上通路"
    source_method: str | None = None
    manual_review_reason: str | None = None

    @property
    def unit_price_per_g(self) -> Decimal:
        return self.package_price / self.package_weight_g


@dataclass(frozen=True)
class ReferenceRecord:
    source_record_id: str
    item_name: str
    market_name: str | None
    original_price: Decimal
    original_unit: str
    price_per_kg: Decimal
    observed_on: date
    region: str | None
    source_url: str

    @property
    def unit_price_per_g(self) -> Decimal:
        return self.price_per_kg / Decimal("1000")


def now_taipei() -> datetime:
    return datetime.now(TAIPEI)


def batch_name(source: str, method: str, when: datetime | None = None) -> str:
    when = when or now_taipei()
    return f"{source}_{method}_{when:%m%d_%H%M%S_%f}"


def open_with_retry(
    request: Request | str,
    *,
    timeout: int = 30,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
):
    if max_attempts < 1:
        raise ValueError("max_attempts 必須大於 0")
    for attempt in range(max_attempts):
        try:
            return urlopen(request, timeout=timeout)
        except HTTPError as exc:
            if exc.code in {401, 403, 429}:
                raise SourceAccessBlocked(
                    f"來源拒絕存取（HTTP {exc.code}），已停止且不嘗試繞過限制"
                ) from exc
            if exc.code < 500 or attempt == max_attempts - 1:
                raise
        except (URLError, TimeoutError):
            if attempt == max_attempts - 1:
                raise
        time.sleep(backoff_seconds * (2**attempt))
    raise RuntimeError("有限重試結束但未取得回應")


def ensure_no_access_challenge(text: str) -> None:
    lowered = text.casefold()
    if any(word in lowered for word in ACCESS_CHALLENGE_WORDS):
        raise SourceAccessBlocked("來源要求驗證碼或顯示存取限制，已停止且不嘗試繞過")


def fetch_text(url: str, *, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-TW"})
    with open_with_retry(request, timeout=timeout) as response:
        text = response.read().decode("utf-8-sig")
    ensure_no_access_challenge(text)
    return text


def fetch_json(url: str, *, timeout: int = 30) -> Any:
    return json.loads(fetch_text(url, timeout=timeout))


def normalize_name(value: str) -> str:
    return re.sub(r"[\s()（）_\-—．。、/]+", "", value).casefold()


def is_clean_search_term(value: str) -> bool:
    """別名只能是簡短食材名稱，不接受份量、單位或料理描述。"""
    value = value.strip()
    normalized = normalize_name(value)
    if not 2 <= len(normalized) <= 12:
        return False
    if value in UNSAFE_ALIAS_TERMS or re.search(r"\s", value):
        return False
    if re.search(r"\d|[=+]|[()（）\[\]【】]", value):
        return False
    if re.search(
        r"(?i)(適量|少許|可略|可省略|約|小匙|大匙|茶匙|湯匙|tsp|tbsp|ml|cc|公克|公斤|kg|\bg\b|顆|瓣|片|匙|杯|碗|份)",
        value,
    ):
        return False
    if normalized.endswith(("末", "泥", "絲", "丁", "碎", "蓉", "切花")):
        return False
    return True


def has_variable_weight(value: str) -> bool:
    """辨識不能當成精確每克價格的浮動重量說明。"""
    if any(
        marker in value
        for marker in (
            "約",
            "±",
            "+/-",
            "＋／－",
            "正負",
            "秤重",
            "浮動重量",
            "依實際重量",
            "實際重量為準",
            "重量依實際",
        )
    ):
        return True
    return bool(
        re.search(
            r"(?i)\d+(?:\.\d+)?\s*[-~～]\s*\d+(?:\.\d+)?\s*(公斤|公克|kg|g)",
            value,
        )
    )


def parse_weight_g(value: str) -> Decimal | None:
    """Parse confirmed *total sale-package* weight, never guessed density/count.

    Supports `100g/包 共10包`, `100g x 10包`, and `300g/包`.
    Ambiguous, variable weight, or multiple conflicting weights are rejected.
    """
    if has_variable_weight(value):
        return None
    weight_re = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(公斤|公克|kg|g)(?![a-z])")
    matches = list(weight_re.finditer(value))
    if len(matches) != 1:
        return None
    match = matches[0]
    prefix = value[:match.start()]
    if re.search(r"\d+\s*(包|盒|片|隻|組|袋|罐|條|顆).*$", prefix) and not re.search(
        r"(?:^|\D)1\s*(包|盒|片|隻|組|袋|罐|條|顆).*$", prefix
    ):
        return None
    amount = Decimal(match.group(1))
    if match.group(2).casefold() in {"公斤", "kg"}:
        amount *= Decimal("1000")
    if amount <= 0:
        return None
    tail = value[match.end():]
    # Check explicit quantity notation; do not assume the price is per bag.
    multipliers = []
    x = re.match(r"(?i)\s*[x×*]\s*(\d+)\s*(?:包|袋|盒|入|組|罐|條)?", tail)
    if x:
        multipliers.append(int(x.group(1)))
    pack = re.search(r"(?:/\s*(?:包|袋|盒|罐|條)|每\s*(?:包|袋|盒|罐|條))\s*(?:[，,;；/\s]|共|合計|總計)*\s*(?:共|合計|總計)?\s*(\d+)\s*(?:包|袋|盒|入|組|罐|條)", tail)
    if pack:
        multipliers.append(int(pack.group(1)))
    for standalone in re.finditer(r"(?:共|合計|總計)\s*(\d+)\s*(?:包|袋|盒|入|組|罐|條)", tail):
        multipliers.append(int(standalone.group(1)))
    # Any unrecognized count alongside weight is ambiguous; refuse false unit prices.
    if re.search(r"\d+\s*(?:包|袋|盒|入|組|罐|條)", tail) and not multipliers:
        return None
    if len(set(multipliers)) > 1 or any(n <= 0 for n in multipliers):
        return None
    return amount * (multipliers[0] if multipliers else 1)


def product_matches(product_name: str, terms: list[str]) -> bool:
    normalized_product = normalize_name(product_name)
    if any(word in product_name for word in NON_FOOD_WORDS):
        return False
    if not terms:
        return False
    canonical_name = terms[0].strip()
    canonical = normalize_name(canonical_name)
    clean_terms = [
        normalize_name(term)
        for term in terms
        if is_clean_search_term(term)
    ]
    if not clean_terms or not any(term in normalized_product for term in clean_terms):
        return False
    if any(word in product_name for word in HIGH_RISK_PRODUCT_WORDS.get(canonical_name, ())):
        return False
    if any(word in product_name and word not in terms[0] for word in PROCESSED_STATE_WORDS):
        return False
    if "乾" in product_name and "乾" not in terms[0]:
        return False
    if "熟" in product_name and "熟" not in terms[0]:
        return False
    if "糖" in product_name and "糖" not in terms[0]:
        return False
    return True


def parse_roc_date(value: str) -> date:
    parts = [int(part) for part in re.split(r"[./-]", value)]
    return date(parts[0] + 1911, parts[1], parts[2])


def reference_valid_until(observed_on: date, days: int = 3) -> datetime:
    return datetime.combine(observed_on + timedelta(days=days), datetime.max.time(), TAIPEI)


def retail_valid_until(captured_at: datetime, hours: int = 48) -> datetime:
    return captured_at + timedelta(hours=hours)


class JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._buffer: list[str] = []
        self.documents: list[Any] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type", "").casefold() == "application/ld+json":
            self._capture = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            try:
                self.documents.append(json.loads("".join(self._buffer)))
            except json.JSONDecodeError:
                pass
            self._capture = False
            self._buffer = []


def json_ld_documents(html: str) -> list[Any]:
    parser = JsonLdParser()
    parser.feed(html)
    return parser.documents
