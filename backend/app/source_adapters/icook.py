"""Fetch one public iCook recipe and prepare T01 rows for review."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from html.parser import HTMLParser

import httpx

from app.services.recipe_import import UNIT_ALIASES, VALID_UNITS


ORIGIN = "https://icook.tw"
ROBOT = "OvercookedRecipePreview"
USER_AGENT = f"{ROBOT}/1.0"
RECIPE_URL = re.compile(r"https://icook\.tw/recipes/([1-9][0-9]{0,11})/?")
MASS_UNITS = {"mg": Decimal("0.001"), "g": Decimal(1), "kg": Decimal(1000)}
SOURCE_UNIT_ALIASES = {"毫克": "mg", "公斤": "kg", "千克": "kg", "KG": "kg", "ML": "ml"}
QUANTITY = re.compile(r"([0-9]+\s*/\s*[0-9]+|[0-9]+(?:\.[0-9]+)?)\s*([^0-9\s].*)?")


class ICookError(ValueError):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status


def _invalid_page() -> ICookError:
    return ICookError("ICOOK_PAGE_UNSUPPORTED", "iCook 頁面缺少可核對的食譜、食材或步驟。", 502)


def _recipe_url(url: str) -> tuple[str, str]:
    match = RECIPE_URL.fullmatch(url.strip())
    if not match:
        raise ICookError("INVALID_INPUT", "請輸入 https://icook.tw/recipes/數字 格式的網址。", 422)
    recipe_id = match[1]
    return f"{ORIGIN}/recipes/{recipe_id}", recipe_id


def _robots_allows(robots: str, path: str) -> bool:
    # iCook uses grouped rules with * wildcards. The most specific matching path wins.
    groups: dict[str, list[tuple[bool, str]]] = {}
    agents: list[str] = []
    saw_rule = False
    for line in robots.splitlines():
        field, _, value = line.split("#", 1)[0].partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent" and value:
            if saw_rule:
                agents, saw_rule = [], False
            agents.append(value.lower())
            groups.setdefault(value.lower(), [])
        elif field in ("allow", "disallow") and agents:
            saw_rule = True
            if value.startswith("/"):
                for agent in agents:
                    groups[agent].append((field == "allow", value))

    rules = groups.get(ROBOT.lower(), groups.get("*", []))
    matches = [(0, True)]
    for allow, rule in rules:
        literal = rule.removesuffix("$")
        pattern = re.escape(literal).replace(r"\*", ".*")
        if re.match(pattern + ("$" if rule.endswith("$") else ""), path):
            matches.append((len(literal.replace("*", "")), allow))
    return max(matches)[1]


def _download(client: httpx.Client, url: str, content_type: str, limit: int) -> bytes:
    try:
        with client.stream("GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=False) as response:
            response.raise_for_status()
            if response.headers.get("content-type", "").split(";")[0].strip().lower() != content_type:
                raise _invalid_page()
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=16_384):
                data.extend(chunk)
                if len(data) > limit:
                    raise ICookError("ICOOK_PAGE_TOO_LARGE", "iCook 回應超出允許大小。", 502)
            return bytes(data)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410) and url != f"{ORIGIN}/robots.txt":
            raise ICookError("ICOOK_NOT_FOUND", "找不到這道 iCook 食譜。", 404) from exc
        raise ICookError("ICOOK_FETCH_FAILED", f"iCook 回傳 HTTP {exc.response.status_code}。", 502) from exc
    except httpx.TimeoutException as exc:
        raise ICookError("ICOOK_FETCH_TIMEOUT", "iCook 連線逾時。", 504) from exc
    except httpx.RequestError as exc:
        raise ICookError("ICOOK_FETCH_FAILED", "無法連線至 iCook。", 502) from exc


def fetch_recipe(url: str, *, transport: httpx.BaseTransport | None = None) -> dict:
    canonical_url, recipe_id = _recipe_url(url)
    with httpx.Client(timeout=8, trust_env=False, transport=transport) as client:
        robots = _download(client, f"{ORIGIN}/robots.txt", "text/plain", 512 * 1024)
        if not _robots_allows(robots.decode("utf-8-sig", errors="replace"), f"/recipes/{recipe_id}"):
            raise ICookError("ICOOK_ROBOTS_BLOCKED", "iCook robots.txt 禁止擷取這個路徑。", 403)
        page = _download(client, canonical_url, "text/html", 2 * 1024 * 1024)
    return parse_recipe(canonical_url, page, observed_at=datetime.now(timezone.utc))


class _RecipeHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = None
        self.values: dict[str, list[str]] = {}
        self._capture: tuple[str, str, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href")
        if self._capture:
            return
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._capture = (tag, "jsonld", [])
        elif tag == "h1" and attrs.get("id") == "recipe-name":
            self._capture = (tag, "title", [])
        elif tag == "div":
            for name in (attrs.get("class") or "").split():
                if name in ("ingredient-name", "ingredient-unit"):
                    self._capture = (tag, name, [])
                    break

    def handle_data(self, data):
        if self._capture:
            self._capture[2].append(data)

    def handle_endtag(self, tag):
        if self._capture and self._capture[0] == tag:
            _, field, parts = self._capture
            self.values.setdefault(field, []).append("".join(parts).strip())
            self._capture = None


def _recipe_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _recipe_nodes(item)
    elif isinstance(value, dict):
        kinds = value.get("@type")
        if kinds == "Recipe" or isinstance(kinds, list) and "Recipe" in kinds:
            yield value
        yield from _recipe_nodes(value.get("@graph"))


def _instruction_text(value) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list) and value:
        return [text for item in value for text in _instruction_text(item)]
    if isinstance(value, dict):
        return _instruction_text(value.get("itemListElement", value.get("text")))
    raise _invalid_page()


def _labels(value) -> str | None:
    if isinstance(value, list):
        labels = [_labels(item) for item in value]
        return "；".join(label for label in labels if label) or None
    if isinstance(value, str):
        return value.strip() or None
    if type(value) is int:
        return str(value)
    return None


def _clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _measure(raw: str) -> tuple[str | None, str | None, str | None]:
    value = unicodedata.normalize("NFKC", raw).strip().replace("⁄", "/")
    match = QUANTITY.fullmatch(value)
    if not match:
        return None, None, None
    number, unit = match.groups()
    unit = SOURCE_UNIT_ALIASES.get(unit, UNIT_ALIASES.get(unit, unit))
    if unit and unit not in VALID_UNITS and unit not in MASS_UNITS:
        return None, None, None
    try:
        fraction = Fraction("".join(number.split()))
    except (ValueError, ZeroDivisionError):
        return None, None, None
    # Keep exactly representable quantities within the T01 scale. Ranges and
    # nonterminating fractions stay in raw_text for review.
    if fraction >= 10**10 or 10**6 % fraction.denominator:
        return None, unit, None
    amount = Decimal(fraction.numerator) / Decimal(fraction.denominator)
    weight = amount * MASS_UNITS[unit] if unit in MASS_UNITS else None
    if weight is not None and (weight >= 10**10 or weight != weight.quantize(Decimal("0.000001"))):
        weight = None
    return format(amount.normalize(), "f"), unit, format(weight.normalize(), "f") if weight is not None else None


def parse_recipe(url: str, page: bytes, *, observed_at: datetime | None = None) -> dict:
    canonical_url, source_id = _recipe_url(url)
    html = _RecipeHTML()
    try:
        html.feed(page.decode("utf-8-sig"))
        html.close()
        recipes = []
        for raw in html.values.get("jsonld", []):
            try:
                recipes.extend(_recipe_nodes(json.loads(raw)))
            except (ValueError, RecursionError):
                continue
    except (ValueError, RecursionError) as exc:
        raise _invalid_page() from exc
    titles = html.values.get("title", [])
    if html.canonical not in (canonical_url, canonical_url + "/") or len(recipes) != 1 or len(titles) != 1:
        raise _invalid_page()
    recipe = recipes[0]
    name = titles[0]
    names = html.values.get("ingredient-name", [])
    quantities = html.values.get("ingredient-unit", [])
    raw_lines = recipe.get("recipeIngredient")
    if (
        not name or not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= 200
        or any(not isinstance(line, str) or not line.strip() for line in raw_lines)
    ):
        raise _invalid_page()
    try:
        steps = _instruction_text(recipe.get("recipeInstructions"))
    except RecursionError as exc:
        raise _invalid_page() from exc
    if len(steps) > 200:
        raise _invalid_page()

    # Join by the full visible line so a template change cannot shift amounts
    # to a different ingredient. Unmatched source lines are retained as-is.
    visible = {}
    if len(names) == len(quantities):
        visible = {
            _clean(f"{ingredient} {quantity}"): (_clean(ingredient), quantity)
            for ingredient, quantity in zip(names, quantities)
            if ingredient
        }
    recipe_id = f"IC-{source_id}"
    raw_id = f"IC-RAW-{source_id}"
    ingredient_rows = []
    for sequence, raw_line in enumerate(raw_lines, 1):
        ingredient, quantity = visible.get(_clean(raw_line), (None, ""))
        amount, unit, weight = _measure(quantity)
        ingredient_rows.append({
            "recipe_ingredient_id": f"{recipe_id}-I{sequence}", "recipe_id": recipe_id,
            "sequence": sequence, "raw_text": raw_line, "canonical_name": ingredient,
            "Food_ID": None, "amount": amount, "unit": unit, "weight_g": weight,
            "protein_g": None, "carbohydrates_g": None, "fat_g": None, "sugar_g": None,
            "sodium_mg": None, "allergens": None,
        })

    servings = _labels(recipe.get("recipeYield"))
    return {
        "status": "pending_review",
        "source": {
            "url": canonical_url,
            "observed_at": observed_at.isoformat() if observed_at else None,
            "html_sha256": hashlib.sha256(page).hexdigest(),
            "rights_status": "pending_review",
        },
        "tables": {
            "source_archive": [{
                "raw_id": raw_id, "source_name": "iCook 愛料理", "source_url": canonical_url,
                "source_recipe_id": source_id, "recipe_name_raw": name, "servings_raw": servings,
                "ingredients_raw": "\n".join(raw_lines),
                "steps_raw": "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1)),
                "tags_raw": _labels(recipe.get("keywords")),
            }],
            "recipes": [{
                "raw_id": raw_id, "recipe_id": recipe_id, "recipe_name": _clean(name),
                "servings_raw": servings, "category": _labels(recipe.get("recipeCategory")),
                "meal_type": None, "Cuisine": _labels(recipe.get("recipeCuisine")),
                "source_name": "icook", "source_url": canonical_url, "allergens": None,
            }],
            "recipe_steps": [
                {"step_id": f"{recipe_id}-S{i}", "recipe_id": recipe_id, "step_no": i, "instruction": _clean(step)}
                for i, step in enumerate(steps, 1)
            ],
            "recipe_ingredients": ingredient_rows,
            "ingredient_aliases": [],
            "allergens": [],
            "標準食材整合": [],
        },
    }
