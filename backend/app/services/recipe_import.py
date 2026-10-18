from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.models.recipe import (
    Allergen,
    IngredientAlias,
    Recipe,
    RecipeIngredient,
    RecipeStep,
    SourceArchive,
)


SCHEMA_VERSION = "t01-recipe-import-v1"

REQUIRED_COLUMNS = {
    "source_archive": {
        "raw_id",
        "source_name",
        "source_url",
        "source_recipe_id",
        "recipe_name_raw",
        "servings_raw",
        "ingredients_raw",
        "steps_raw",
        "tags_raw",
    },
    "recipes": {
        "raw_id",
        "recipe_id",
        "recipe_name",
        "servings_raw",
        "category",
        "meal_type",
        "Cuisine",
        "source_name",
        "source_url",
        "allergens",
    },
    "recipe_steps": {"step_id", "recipe_id", "step_no", "instruction_clean"},
    "recipe_ingredients": {
        "recipe_ingredient_id",
        "recipe_id",
        "sequence",
        "raw_text",
        "canonical_name",
        "Food_ID",
        "amount",
        "unit",
        "weight_g",
        "protein_g",
        "carbohydrates_g",
        "fat_g",
        "sugar_g",
        "sodium_mg",
        "allergens",
    },
    "ingredient_aliases": {"alias_id", "Food_ID", "canonical_name", "alias"},
    "allergens": {"allergen_id", "allergen_name", "reference"},
}

QUALITATIVE_WORDS = (
    "適量",
    "少許",
    "酌量",
    "若干",
    "隨意",
    "一點",
    "些許",
    "適當",
    "少量",
    "視情況",
    "依喜好",
    "任意",
)

METHOD_KEYWORDS = (
    "氣炸",
    "微波",
    "煎",
    "炒",
    "炸",
    "烤",
    "蒸",
    "煮",
    "燉",
    "滷",
    "燙",
    "拌",
)

UNIT_ALIASES = {
    "公克": "g",
    "克": "g",
    "G": "g",
    "毫升": "ml",
    "cc": "ml",
    "CC": "ml",
    "茶匙": "小匙",
    "湯匙": "大匙",
    "枝": "支",
}

VALID_UNITS = {
    "g",
    "ml",
    "L",
    "公升",
    "大匙",
    "小匙",
    "匙",
    "顆",
    "片",
    "條",
    "根",
    "瓣",
    "支",
    "個",
    "碗",
    "隻",
    "杯",
    "把",
    "朵",
    "盒",
    "包",
    "斤",
    "塊",
    "粒",
    "小塊",
    "株",
    "罐",
    "尾",
    "人份",
    "瓶",
    "份",
    "兩",
    "張",
    "米杯",
    "小撮",
    "滴",
    "小把",
    "小飯碗",
    "大把",
    "段",
    "蓋",
    "小粒",
    "對",
    "大株",
    "大瓣",
    "大片",
}

RANGE_RE = re.compile(r"(?P<low>\d+(?:\.\d+)?)\s*[~～\-–—]\s*(?P<high>\d+(?:\.\d+)?)")
FRACTION_RE = re.compile(r"(?P<num>\d+)\s*/\s*(?P<den>\d+)")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
UNIT_IN_TEXT_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:/\s*\d+)?|\d+(?:\.\d+)?\s*[~～\-–—]\s*\d+(?:\.\d+)?)\s*"
    r"(?P<unit>公克|克|g|G|毫升|ml|cc|CC|大匙|湯匙|小匙|茶匙|顆|片|條|根|瓣|支|個|碗|隻|杯|把|朵|盒|包|斤|塊|粒|株|罐|尾|份|兩|張|米杯|小撮|滴)"
)


class WorkbookValidationError(ValueError):
    """Raised when the workbook contract is incomplete or inconsistent."""


@dataclass(frozen=True)
class ImportBundle:
    source_archive: list[dict[str, Any]]
    recipes: list[dict[str, Any]]
    recipe_steps: list[dict[str, Any]]
    recipe_ingredients: list[dict[str, Any]]
    ingredient_aliases: list[dict[str, Any]]
    allergens: list[dict[str, Any]]
    report: dict[str, Any]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise WorkbookValidationError(f"Invalid numeric value: {value!r}") from exc


def _integer(value: Any, field: str) -> int:
    number = _decimal(value)
    if number is None or number != number.to_integral_value():
        raise WorkbookValidationError(f"{field} must be an integer: {value!r}")
    return int(number)


def _split_list(value: Any) -> list[str]:
    text_value = _text(value)
    if text_value is None:
        return []
    return [item.strip() for item in re.split(r"[；;]", text_value) if item.strip()]


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(character for character in normalized if character.isalnum())


def standard_dish_id(recipe_name: str) -> str:
    normalized = normalize_name(recipe_name)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12].upper()
    return f"DISH{digest}"


def _content_hash(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sheet_records(workbook: Any, sheet_name: str) -> list[dict[str, Any]]:
    if sheet_name not in workbook.sheetnames:
        raise WorkbookValidationError(f"Missing worksheet: {sheet_name}")
    worksheet = workbook[sheet_name]
    rows = worksheet.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration as exc:
        raise WorkbookValidationError(f"Worksheet is empty: {sheet_name}") from exc

    headers = [_text(value) for value in header_row]
    if any(header is None for header in headers):
        raise WorkbookValidationError(f"Worksheet has blank header: {sheet_name}")
    missing = REQUIRED_COLUMNS[sheet_name] - set(headers)
    if missing:
        raise WorkbookValidationError(
            f"Worksheet {sheet_name} missing columns: {', '.join(sorted(missing))}"
        )

    records: list[dict[str, Any]] = []
    for row_number, values in enumerate(rows, start=2):
        if all(value is None or value == "" for value in values):
            continue
        record = {str(header): value for header, value in zip(headers, values)}
        record["__row__"] = row_number
        records.append(record)
    return records


def _parse_servings(value: Any) -> tuple[Decimal | None, str, list[str]]:
    raw = _text(value)
    if raw is None:
        return None, "missing", ["missing_servings"]
    match = NUMBER_RE.search(raw)
    if match is None:
        return None, "unresolved", ["unresolved_servings"]
    servings = Decimal(match.group())
    issues = []
    status = "parsed"
    if servings <= 0:
        issues.append("nonpositive_servings")
        status = "invalid"
    elif servings > 12:
        issues.append("servings_outlier_review_required")
        status = "review_required"
    return servings, status, issues


def _normalize_unit(source_unit: str | None, raw_text: str) -> tuple[str | None, str, list[str]]:
    issues: list[str] = []
    if source_unit is None:
        return None, "missing", ["missing_unit"]
    candidate = UNIT_ALIASES.get(source_unit, source_unit)
    if candidate in VALID_UNITS:
        status = "normalized" if candidate != source_unit else "source"
        return candidate, status, issues

    match = UNIT_IN_TEXT_RE.search(raw_text)
    if match:
        parsed = UNIT_ALIASES.get(match.group("unit"), match.group("unit"))
        if parsed in VALID_UNITS:
            issues.append("unit_corrected_from_raw_text")
            return parsed, "corrected_from_raw", issues
    issues.append("invalid_unit")
    return None, "invalid", issues


def _normalize_quantity(record: dict[str, Any]) -> dict[str, Any]:
    raw_text = _text(record.get("raw_text")) or ""
    source_amount = _decimal(record.get("amount"))
    source_unit = _text(record.get("unit"))
    source_weight = _decimal(record.get("weight_g"))
    unit, unit_status, issues = _normalize_unit(source_unit, raw_text)

    contains_qualitative = any(word in raw_text for word in QUALITATIVE_WORDS)
    range_match = RANGE_RE.search(raw_text)
    fraction_match = FRACTION_RE.search(raw_text)
    has_digit = NUMBER_RE.search(raw_text) is not None
    amount: Decimal | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None

    if contains_qualitative and not has_digit:
        quantity_status = "qualitative_unresolved"
        issues.append("qualitative_quantity_without_number")
    elif range_match:
        amount_min = Decimal(range_match.group("low"))
        amount_max = Decimal(range_match.group("high"))
        if amount_min > amount_max:
            amount_min, amount_max = amount_max, amount_min
        quantity_status = "source_range"
        issues.append("range_requires_usage_policy")
    elif fraction_match:
        denominator = Decimal(fraction_match.group("den"))
        if denominator == 0:
            quantity_status = "invalid"
            issues.append("invalid_fraction")
        else:
            amount = Decimal(fraction_match.group("num")) / denominator
            quantity_status = "source_exact"
    elif has_digit and source_amount is not None:
        amount = source_amount
        quantity_status = "source_exact"
    else:
        quantity_status = "unresolved"
        issues.append("quantity_not_supported_by_raw_text")

    weight_g: Decimal | None = None
    if unit == "g" and amount is not None and quantity_status == "source_exact":
        weight_g = amount
        weight_status = "source_explicit"
    elif unit == "g" and quantity_status == "source_range":
        weight_status = "source_range"
    elif source_weight is not None:
        weight_status = "unverified_conversion"
        issues.append("source_weight_requires_conversion_evidence")
    else:
        weight_status = "unresolved"

    return {
        "source_amount": source_amount,
        "source_unit": source_unit,
        "source_weight_g": source_weight,
        "amount": amount,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "unit": unit,
        "quantity_status": quantity_status,
        "unit_status": unit_status,
        "weight_g": weight_g,
        "weight_status": weight_status,
        "quality_issues": sorted(set(issues)),
    }


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text_value = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkbookValidationError(f"Invalid datetime value: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _method_candidates(instructions: Iterable[str]) -> list[str]:
    text_value = "\n".join(instructions)
    return [method for method in METHOD_KEYWORDS if method in text_value]


def _ingredient_similarity(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _suspected_duplicates(
    recipe_rows: list[dict[str, Any]], ingredients_by_recipe: dict[str, set[str]]
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(recipe_rows):
        for right in recipe_rows[index + 1 :]:
            if left["standard_dish_id"] == right["standard_dish_id"]:
                continue
            left_name = left["normalized_name"]
            right_name = right["normalized_name"]
            name_score = SequenceMatcher(None, left_name, right_name).ratio()
            ingredient_score = _ingredient_similarity(
                ingredients_by_recipe.get(left["recipe_id"], set()),
                ingredients_by_recipe.get(right["recipe_id"], set()),
            )
            contains = (
                min(len(left_name), len(right_name)) >= 3
                and (left_name in right_name or right_name in left_name)
            )
            if (name_score >= 0.72 and ingredient_score >= 0.35) or (
                contains and ingredient_score >= 0.35
            ):
                pairs.append(
                    {
                        "recipe_id_a": left["recipe_id"],
                        "recipe_id_b": right["recipe_id"],
                        "name_similarity": round(name_score, 4),
                        "ingredient_jaccard": round(ingredient_score, 4),
                        "status": "manual_review_required",
                    }
                )
    return pairs


def parse_recipe_workbook(path: str | Path) -> ImportBundle:
    workbook_path = Path(path).resolve()
    if not workbook_path.is_file():
        raise FileNotFoundError(workbook_path)

    source_sha256 = _file_hash(workbook_path)
    import_batch_id = source_sha256
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        raw_sheets = {
            sheet_name: _sheet_records(workbook, sheet_name)
            for sheet_name in REQUIRED_COLUMNS
        }
    finally:
        workbook.close()

    source_input = raw_sheets["source_archive"]
    recipe_input = raw_sheets["recipes"]
    step_input = raw_sheets["recipe_steps"]
    ingredient_input = raw_sheets["recipe_ingredients"]
    alias_input = raw_sheets["ingredient_aliases"]
    allergen_input = raw_sheets["allergens"]

    def require_unique(records: list[dict[str, Any]], key: str, sheet: str) -> None:
        values = [_text(record.get(key)) for record in records]
        missing = [record["__row__"] for record, value in zip(records, values) if value is None]
        duplicates = sorted(value for value, count in Counter(values).items() if value and count > 1)
        if missing:
            raise WorkbookValidationError(f"{sheet}.{key} blank at rows: {missing}")
        if duplicates:
            raise WorkbookValidationError(f"{sheet}.{key} duplicates: {duplicates[:10]}")

    require_unique(source_input, "raw_id", "source_archive")
    require_unique(recipe_input, "recipe_id", "recipes")
    require_unique(step_input, "step_id", "recipe_steps")
    require_unique(ingredient_input, "recipe_ingredient_id", "recipe_ingredients")
    require_unique(allergen_input, "allergen_id", "allergens")

    source_by_raw_id = {_text(row["raw_id"]): row for row in source_input}
    recipe_ids = {_text(row["recipe_id"]) for row in recipe_input}
    orphan_raw_ids = sorted(
        _text(row["raw_id"])
        for row in recipe_input
        if _text(row["raw_id"]) not in source_by_raw_id
    )
    orphan_step_ids = sorted(
        _text(row["recipe_id"])
        for row in step_input
        if _text(row["recipe_id"]) not in recipe_ids
    )
    orphan_ingredient_ids = sorted(
        _text(row["recipe_id"])
        for row in ingredient_input
        if _text(row["recipe_id"]) not in recipe_ids
    )
    if orphan_raw_ids or orphan_step_ids or orphan_ingredient_ids:
        raise WorkbookValidationError(
            "Workbook contains orphan references: "
            f"raw={orphan_raw_ids[:10]}, steps={orphan_step_ids[:10]}, "
            f"ingredients={orphan_ingredient_ids[:10]}"
        )

    source_rows: list[dict[str, Any]] = []
    source_quality_by_raw: dict[str, list[str]] = {}
    for row in source_input:
        raw_id = _text(row["raw_id"])
        assert raw_id is not None
        rights_status = (_text(row.get("rights_status")) or "unknown").lower()
        review_status = (_text(row.get("review_status")) or "pending").lower()
        issues: list[str] = []
        if not _text(row.get("license_name")):
            issues.append("missing_license")
        if rights_status not in {"verified", "approved"}:
            issues.append("rights_not_verified")
        if not _text(row.get("source_version")):
            issues.append("missing_source_version")
        if not _parse_datetime(row.get("retrieved_at")):
            issues.append("missing_retrieved_at")
        if review_status != "approved":
            issues.append("source_review_pending")
        source_quality_by_raw[raw_id] = issues
        content_fields = {
            key: row.get(key)
            for key in (
                "source_name",
                "source_url",
                "source_recipe_id",
                "recipe_name_raw",
                "servings_raw",
                "ingredients_raw",
                "steps_raw",
                "tags_raw",
            )
        }
        source_rows.append(
            {
                "raw_id": raw_id,
                "source_name": _text(row["source_name"]),
                "source_url": _text(row["source_url"]),
                "source_recipe_id": _text(row["source_recipe_id"]),
                "recipe_name_raw": _text(row["recipe_name_raw"]),
                "servings_raw": _text(row["servings_raw"]),
                "ingredients_raw": _text(row["ingredients_raw"]),
                "steps_raw": _text(row["steps_raw"]),
                "tags_raw": _text(row["tags_raw"]),
                "license_name": _text(row.get("license_name")),
                "rights_uri": _text(row.get("rights_uri")),
                "rights_status": rights_status,
                "retrieved_at": _parse_datetime(row.get("retrieved_at")),
                "source_version": _text(row.get("source_version")),
                "content_hash": _content_hash(content_fields),
                "import_batch_id": import_batch_id,
                "review_status": review_status,
                "quality_issues": issues,
            }
        )

    step_rows: list[dict[str, Any]] = []
    instructions_by_recipe: dict[str, list[str]] = defaultdict(list)
    step_numbers: dict[str, set[int]] = defaultdict(set)
    for row in step_input:
        recipe_id = _text(row["recipe_id"])
        instruction = _text(row["instruction_clean"])
        if recipe_id is None or instruction is None:
            raise WorkbookValidationError(f"Invalid recipe step at row {row['__row__']}")
        step_no = _integer(row["step_no"], "step_no")
        if step_no in step_numbers[recipe_id]:
            raise WorkbookValidationError(f"Duplicate step number {recipe_id}/{step_no}")
        step_numbers[recipe_id].add(step_no)
        instructions_by_recipe[recipe_id].append(instruction)
        cooking_method = _text(row.get("cooking_method"))
        step_rows.append(
            {
                "step_id": _text(row["step_id"]),
                "recipe_id": recipe_id,
                "step_no": step_no,
                "instruction_clean": instruction,
                "cooking_method": cooking_method,
                "method_status": (_text(row.get("method_status")) or "unreviewed").lower(),
                "review_status": (_text(row.get("review_status")) or "pending").lower(),
            }
        )

    ingredient_rows: list[dict[str, Any]] = []
    ingredients_by_recipe: dict[str, set[str]] = defaultdict(set)
    ingredient_numbers: dict[str, set[Decimal]] = defaultdict(set)
    food_names: dict[str, set[str]] = defaultdict(set)
    ingredient_issues_by_recipe: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ingredient_input:
        ingredient_id = _text(row["recipe_ingredient_id"])
        recipe_id = _text(row["recipe_id"])
        food_id = _text(row["Food_ID"])
        raw_text = _text(row["raw_text"])
        canonical_name = _text(row["canonical_name"])
        if not all((ingredient_id, recipe_id, food_id, raw_text, canonical_name)):
            raise WorkbookValidationError(f"Invalid recipe ingredient at row {row['__row__']}")
        sequence = _decimal(row["sequence"])
        if sequence is None or sequence <= 0:
            raise WorkbookValidationError(
                f"sequence must be a positive number: {row['sequence']!r}"
            )
        if sequence in ingredient_numbers[recipe_id]:
            raise WorkbookValidationError(f"Duplicate ingredient sequence {recipe_id}/{sequence}")
        ingredient_numbers[recipe_id].add(sequence)
        ingredients_by_recipe[recipe_id].add(food_id)
        food_names[food_id].add(canonical_name)
        quantity = _normalize_quantity(row)
        adjustment_status = (_text(row.get("adjustment_status")) or "not_reviewed").lower()
        adjustable_min = _decimal(row.get("adjustable_min"))
        adjustable_max = _decimal(row.get("adjustable_max"))
        if adjustable_min is not None and adjustable_max is not None and adjustable_min > adjustable_max:
            quantity["quality_issues"].append("invalid_adjustable_range")
        if adjustment_status != "approved":
            quantity["quality_issues"].append("adjustable_range_not_reviewed")
        quality_issues = sorted(set(quantity.pop("quality_issues")))
        if quality_issues:
            ingredient_issues_by_recipe[recipe_id].append(
                {
                    "recipe_ingredient_id": ingredient_id,
                    "food_id": food_id,
                    "raw_text": raw_text,
                    "issues": quality_issues,
                }
            )
        ingredient_rows.append(
            {
                "recipe_ingredient_id": ingredient_id,
                "recipe_id": recipe_id,
                "sequence": sequence,
                "raw_text": raw_text,
                "canonical_name": canonical_name,
                "food_id": food_id,
                **quantity,
                "adjustable_min": adjustable_min,
                "adjustable_max": adjustable_max,
                "adjustment_status": adjustment_status,
                "is_required": row.get("is_required") if isinstance(row.get("is_required"), bool) else None,
                "source_protein_g": _decimal(row.get("protein_g")),
                "source_carbohydrates_g": _decimal(row.get("carbohydrates_g")),
                "source_fat_g": _decimal(row.get("fat_g")),
                "source_sugar_g": _decimal(row.get("sugar_g")),
                "source_sodium_mg": _decimal(row.get("sodium_mg")),
                "allergens": _split_list(row.get("allergens")),
                "quality_issues": quality_issues,
            }
        )

    canonical_conflicts = {
        food_id: sorted(names) for food_id, names in food_names.items() if len(names) > 1
    }
    if canonical_conflicts:
        raise WorkbookValidationError(
            f"Food_ID canonical name conflicts: {dict(list(canonical_conflicts.items())[:10])}"
        )

    alias_targets: dict[str, set[str]] = defaultdict(set)
    aliases_by_food: dict[str, set[str]] = defaultdict(set)
    for row in alias_input:
        food_id = _text(row["Food_ID"])
        alias = _text(row["alias"])
        canonical_name = _text(row["canonical_name"])
        if not all((food_id, alias, canonical_name)):
            raise WorkbookValidationError(f"Invalid ingredient alias at row {row['__row__']}")
        if food_id in food_names and canonical_name not in food_names[food_id]:
            raise WorkbookValidationError(
                f"Ingredient alias canonical name conflicts for {food_id}: {canonical_name}"
            )
        alias_targets[normalize_name(alias)].add(food_id)
        aliases_by_food[food_id].add(alias)

    ingredient_alias_rows: list[dict[str, Any]] = []
    ambiguous_alias_count = 0
    for food_id in sorted(food_names):
        aliases: list[str] = []
        ambiguous_aliases: list[str] = []
        for alias in sorted(aliases_by_food.get(food_id, set())):
            if len(alias_targets[normalize_name(alias)]) == 1:
                aliases.append(alias)
            else:
                ambiguous_aliases.append(alias)
                ambiguous_alias_count += 1
        issues = ["ambiguous_aliases_require_review"] if ambiguous_aliases else []
        ingredient_alias_rows.append(
            {
                "food_id": food_id,
                "canonical_name": next(iter(food_names[food_id])),
                "aliases": aliases,
                "ambiguous_aliases": ambiguous_aliases,
                "review_status": "pending",
                "quality_issues": issues,
            }
        )

    recipe_rows: list[dict[str, Any]] = []
    recipe_by_id: dict[str, dict[str, Any]] = {}
    normalized_groups: dict[str, list[str]] = defaultdict(list)
    for row in recipe_input:
        recipe_id = _text(row["recipe_id"])
        raw_id = _text(row["raw_id"])
        recipe_name = _text(row["recipe_name"])
        if not all((recipe_id, raw_id, recipe_name)):
            raise WorkbookValidationError(f"Invalid recipe at row {row['__row__']}")
        source = source_by_raw_id[raw_id]
        servings, servings_status, servings_issues = _parse_servings(row["servings_raw"])
        normalized = normalize_name(recipe_name)
        normalized_groups[normalized].append(recipe_id)
        raw_name = _text(source["recipe_name_raw"])
        name_aliases = [raw_name] if raw_name and raw_name != recipe_name else []
        supplied_methods = _split_list(row.get("cooking_methods"))
        methods = supplied_methods or _method_candidates(instructions_by_recipe.get(recipe_id, []))
        method_status = (_text(row.get("cooking_method_status")) or "unreviewed").lower()
        review_status = (_text(row.get("review_status")) or "pending").lower()
        quality_issues = list(source_quality_by_raw[raw_id]) + servings_issues
        if not instructions_by_recipe.get(recipe_id):
            quality_issues.append("missing_steps")
        if not ingredients_by_recipe.get(recipe_id):
            quality_issues.append("missing_ingredients")
        if not methods:
            quality_issues.append("missing_cooking_method")
        if method_status != "approved":
            quality_issues.append("cooking_method_not_reviewed")
        if review_status != "approved":
            quality_issues.append("recipe_review_pending")
        for ingredient_issue in ingredient_issues_by_recipe.get(recipe_id, []):
            for issue in ingredient_issue["issues"]:
                quality_issues.append(f"ingredient:{issue}")

        recipe_row = {
            "recipe_id": recipe_id,
            "raw_id": raw_id,
            "standard_dish_id": standard_dish_id(recipe_name),
            "recipe_name": recipe_name,
            "normalized_name": normalized,
            "name_aliases": name_aliases,
            "servings_raw": _text(row["servings_raw"]),
            "original_servings": servings,
            "servings_status": servings_status,
            "category": _text(row["category"]),
            "meal_type": _text(row["meal_type"]),
            "cuisine": _text(row["Cuisine"]),
            "source_name": _text(row["source_name"]),
            "source_url": _text(row["source_url"]),
            "allergens": _split_list(row.get("allergens")),
            "cooking_methods": methods,
            "cooking_method_status": method_status,
            "duplicate_status": "unique_candidate",
            "duplicate_of_recipe_id": None,
            "duplicate_candidate_recipe_ids": [],
            "review_status": review_status,
            "quality_status": "not_qualified",
            "is_eligible": False,
            "quality_issues": sorted(set(quality_issues)),
        }
        recipe_rows.append(recipe_row)
        recipe_by_id[recipe_id] = recipe_row

    exact_duplicate_groups: list[dict[str, Any]] = []
    for normalized_name, ids in sorted(normalized_groups.items()):
        if len(ids) <= 1:
            continue
        ids = sorted(ids)
        primary_id = ids[0]
        exact_duplicate_groups.append(
            {
                "recipe_ids": ids,
                "primary_recipe_id": primary_id,
                "status": "manual_review_required",
            }
        )
        for recipe_id in ids:
            recipe_by_id[recipe_id]["duplicate_status"] = "exact_name_duplicate"
            recipe_by_id[recipe_id]["duplicate_candidate_recipe_ids"] = [
                candidate for candidate in ids if candidate != recipe_id
            ]
            recipe_by_id[recipe_id]["quality_issues"].append("exact_name_duplicate_review_required")
            if recipe_id != primary_id:
                recipe_by_id[recipe_id]["duplicate_of_recipe_id"] = primary_id

    suspected_pairs = _suspected_duplicates(recipe_rows, ingredients_by_recipe)
    for pair in suspected_pairs:
        left = recipe_by_id[pair["recipe_id_a"]]
        right = recipe_by_id[pair["recipe_id_b"]]
        if left["duplicate_status"] == "unique_candidate":
            left["duplicate_status"] = "suspected_duplicate"
        if right["duplicate_status"] == "unique_candidate":
            right["duplicate_status"] = "suspected_duplicate"
        left["duplicate_candidate_recipe_ids"].append(right["recipe_id"])
        right["duplicate_candidate_recipe_ids"].append(left["recipe_id"])
        left["quality_issues"].append("suspected_duplicate_review_required")
        right["quality_issues"].append("suspected_duplicate_review_required")

    for recipe in recipe_rows:
        recipe["duplicate_candidate_recipe_ids"] = sorted(
            set(recipe["duplicate_candidate_recipe_ids"])
        )
        recipe["quality_issues"] = sorted(set(recipe["quality_issues"]))
        blocking = {
            "missing_license",
            "rights_not_verified",
            "missing_source_version",
            "missing_retrieved_at",
            "source_review_pending",
            "recipe_review_pending",
            "missing_steps",
            "missing_ingredients",
            "missing_cooking_method",
            "cooking_method_not_reviewed",
            "ingredient:invalid_unit",
            "ingredient:quantity_not_supported_by_raw_text",
            "ingredient:qualitative_quantity_without_number",
            "ingredient:adjustable_range_not_reviewed",
        }
        recipe["is_eligible"] = not any(issue in blocking for issue in recipe["quality_issues"])
        recipe["quality_status"] = "qualified" if recipe["is_eligible"] else "not_qualified"

    allergen_rows = []
    for row in allergen_input:
        allergen_id = _text(row["allergen_id"])
        allergen_name = _text(row["allergen_name"])
        reference = _text(row["reference"])
        if not all((allergen_id, allergen_name, reference)):
            raise WorkbookValidationError(f"Invalid allergen at row {row['__row__']}")
        allergen_rows.append(
            {
                "allergen_id": allergen_id,
                "allergen_name": allergen_name,
                "reference": reference,
                "source_version": _text(row.get("source_version")),
                "review_status": (_text(row.get("review_status")) or "pending").lower(),
            }
        )

    qualified = sum(1 for recipe in recipe_rows if recipe["is_eligible"])
    reason_counts = Counter(
        issue for recipe in recipe_rows if not recipe["is_eligible"] for issue in recipe["quality_issues"]
    )
    exact_duplicate_records = sum(len(group["recipe_ids"]) - 1 for group in exact_duplicate_groups)
    ingredient_ids_by_reason: dict[str, list[str]] = defaultdict(list)
    for recipe_issues in ingredient_issues_by_recipe.values():
        for issue in recipe_issues:
            for reason in issue["issues"]:
                ingredient_ids_by_reason[reason].append(issue["recipe_ingredient_id"])
    report = {
        "schema_version": SCHEMA_VERSION,
        "source_file": workbook_path.name,
        "source_sha256": source_sha256,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "acquired_recipes": len(recipe_rows),
            "deduplicated_standard_dishes": len(recipe_rows) - exact_duplicate_records,
            "exact_duplicate_records": exact_duplicate_records,
            "exact_duplicate_groups": len(exact_duplicate_groups),
            "suspected_duplicate_pairs": len(suspected_pairs),
            "qualified_recipes": qualified,
            "not_qualified_recipes": len(recipe_rows) - qualified,
            "recipe_steps": len(step_rows),
            "recipe_ingredients": len(ingredient_rows),
            "distinct_food_ids": len(ingredient_alias_rows),
            "ambiguous_alias_assignments": ambiguous_alias_count,
        },
        "not_qualified_reason_counts": dict(sorted(reason_counts.items())),
        "duplicate_review": {
            "exact_name_groups": exact_duplicate_groups,
            "suspected_pairs": suspected_pairs,
        },
        "manual_review": {
            "source_records": [
                {"raw_id": row["raw_id"], "issues": row["quality_issues"]}
                for row in source_rows
                if row["quality_issues"]
            ],
            "recipes": [
                {
                    "recipe_id": row["recipe_id"],
                    "issues": row["quality_issues"],
                }
                for row in recipe_rows
                if row["quality_issues"]
            ],
            "ingredient_ids_by_reason": {
                reason: sorted(set(ingredient_ids))
                for reason, ingredient_ids in sorted(ingredient_ids_by_reason.items())
            },
            "food_aliases": [
                {
                    "food_id": row["food_id"],
                    "ambiguous_alias_count": len(row["ambiguous_aliases"]),
                }
                for row in ingredient_alias_rows
                if row["ambiguous_aliases"]
            ],
        },
        "g01": {
            "status": "passed" if qualified >= 150 else "not_passed",
            "required_qualified_recipes": 150,
            "actual_qualified_recipes": qualified,
            "gap": max(0, 150 - qualified),
            "reason": (
                "At least 150 recipes have complete provenance and review evidence."
                if qualified >= 150
                else "Source rights/version and human review evidence are incomplete; unverified records are not counted as qualified."
            ),
        },
        "ac05": {
            "status": "passed" if qualified >= 150 else "not_passed",
            "reason": (
                "Import evidence includes provenance, quantities, steps, deduplication and qualified counts."
                if qualified >= 150
                else "The import is reproducible, but source rights/version and human review evidence remain incomplete."
            ),
        },
    }

    return ImportBundle(
        source_archive=source_rows,
        recipes=recipe_rows,
        recipe_steps=step_rows,
        recipe_ingredients=ingredient_rows,
        ingredient_aliases=ingredient_alias_rows,
        allergens=allergen_rows,
        report=report,
    )


def _upsert_rows(
    session: Session, model: type[Any], rows: Iterable[dict[str, Any]], primary_key: str
) -> dict[str, int]:
    inserted = 0
    updated = 0
    for values in rows:
        identity = values[primary_key]
        instance = session.get(model, identity)
        if instance is None:
            session.add(model(**values))
            inserted += 1
        else:
            for key, value in values.items():
                setattr(instance, key, value)
            updated += 1
    return {"inserted": inserted, "updated": updated}


def import_recipe_bundle(session: Session, bundle: ImportBundle) -> dict[str, dict[str, int]]:
    """Idempotently upsert a parsed bundle into the six T01 tables.

    The caller owns the transaction. A failed import can therefore be rolled back as
    one unit, and no source workbook is ever changed.
    """

    results: dict[str, dict[str, int]] = {}
    results["source_archive"] = _upsert_rows(
        session, SourceArchive, bundle.source_archive, "raw_id"
    )
    results["ingredient_aliases"] = _upsert_rows(
        session, IngredientAlias, bundle.ingredient_aliases, "food_id"
    )
    results["allergens"] = _upsert_rows(session, Allergen, bundle.allergens, "allergen_id")
    session.flush()
    results["recipes"] = _upsert_rows(session, Recipe, bundle.recipes, "recipe_id")
    session.flush()
    results["recipe_steps"] = _upsert_rows(
        session, RecipeStep, bundle.recipe_steps, "step_id"
    )
    results["recipe_ingredients"] = _upsert_rows(
        session, RecipeIngredient, bundle.recipe_ingredients, "recipe_ingredient_id"
    )
    session.flush()
    return results


def write_quality_report(report: dict[str, Any], path: str | Path) -> Path:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(report_path)
    return report_path

