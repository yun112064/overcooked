"""T12-only nutrition supplements (user decision 2026-11-02).

Two kinds, both kept out of every shared table and workbook:

* ``values`` – per-100 g values for a recipe Food_ID that has no usable
  TFDA/USDA record in the current import (e.g. 水 0 kcal confirmed by the user,
  or a value copied from a public government database with its URL).
* ``fill`` – fills specific blank cells of an otherwise valid record of a
  nutrition Food_ID (e.g. 香油 protein confirmed 0 for a pure fat).

Every entry needs a provider, a source description, an approver and a date.
A malformed file fails closed; callers report it instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'config' / 't12_nutrition_supplements.json'
SCHEMA_VERSION = 't12-nutrition-supplement-v1'
FOOD_ID = re.compile(r'FOOD\d{6}')
NUTRIENTS = ('energy_kcal', 'protein_g', 'fat_g', 'carbohydrate_g', 'sugars_g', 'sodium_mg', 'dietary_fiber_g')
PROVIDERS = frozenset({'USER_CONFIRMED', 'TFDA', 'USDA', 'MEXT', 'MANUFACTURER_LABEL'})


class NutritionSupplementInvalid(ValueError):
    """The supplement file cannot be trusted."""


@dataclass(frozen=True)
class NutritionSupplement:
    food_id: str
    name: str
    kind: str
    state: str
    values: dict = field(default_factory=dict)
    provider: str = ''
    source_name: str = ''
    source_record_id: str = ''
    source_url: str | None = None
    retrieved_on: str | None = None
    basis: str = ''
    approved_by: str = ''
    approved_on: str = ''
    version: str = ''

    @property
    def record_id(self) -> str:
        return self.source_record_id or f'{self.provider}:{self.food_id}'

    @property
    def provenance(self) -> dict:
        return {'supplement_version': self.version, 'kind': self.kind, 'food_id': self.food_id,
                'name': self.name, 'provider': self.provider, 'source_name': self.source_name,
                'source_record_id': self.source_record_id or None, 'source_url': self.source_url,
                'retrieved_on': self.retrieved_on, 'basis': self.basis,
                'approved_by': self.approved_by, 'approved_on': self.approved_on,
                'values_per_100g': dict(self.values)}


@dataclass(frozen=True)
class NutritionSupplements:
    values: dict[str, NutritionSupplement]
    fills: dict[str, NutritionSupplement]


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_nutrition_supplements(data) -> NutritionSupplements:
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION:
        raise NutritionSupplementInvalid('schema_version')
    version = _text(data.get('version'))
    entries = data.get('entries')
    if version is None or not isinstance(entries, list):
        raise NutritionSupplementInvalid('version or entries')
    values: dict[str, NutritionSupplement] = {}
    fills: dict[str, NutritionSupplement] = {}
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or entry.get('status') not in ('approved', 'pending', 'rejected'):
            raise NutritionSupplementInvalid(f'entry {number}: status')
        if entry['status'] != 'approved':
            continue
        food_id, kind = _text(entry.get('food_id')), entry.get('kind')
        state, provider = entry.get('state'), entry.get('provider')
        if food_id is None or not FOOD_ID.fullmatch(food_id) or kind not in ('values', 'fill'):
            raise NutritionSupplementInvalid(f'entry {number}: food_id or kind')
        if provider not in PROVIDERS or (kind == 'values' and state not in ('raw', 'cooked')):
            raise NutritionSupplementInvalid(f'entry {number}: provider or state')
        raw_values = entry.get('values')
        if not isinstance(raw_values, dict) or not raw_values or set(raw_values) - set(NUTRIENTS):
            raise NutritionSupplementInvalid(f'entry {number}: values')
        if kind == 'values' and not {'energy_kcal', 'protein_g'} <= set(raw_values):
            raise NutritionSupplementInvalid(f'entry {number}: energy and protein required')
        parsed = {}
        for key, value in raw_values.items():
            if not isinstance(value, str):
                raise NutritionSupplementInvalid(f'entry {number}: {key} must be a decimal string')
            try:
                number_value = Decimal(value)
            except InvalidOperation:
                raise NutritionSupplementInvalid(f'entry {number}: {key}') from None
            if not number_value.is_finite() or number_value < 0:
                raise NutritionSupplementInvalid(f'entry {number}: {key}')
            parsed[key] = value
        source_name, basis = _text(entry.get('source_name')), _text(entry.get('basis'))
        approver, approved_on = _text(entry.get('approved_by')), _text(entry.get('approved_on'))
        url, retrieved = _text(entry.get('source_url')), _text(entry.get('retrieved_on'))
        if not all((source_name, basis, approver, approved_on)):
            raise NutritionSupplementInvalid(f'entry {number}: source/approval record')
        if provider != 'USER_CONFIRMED' and not (url and retrieved):
            raise NutritionSupplementInvalid(f'entry {number}: public data needs source_url and retrieved_on')
        try:
            date.fromisoformat(approved_on)  # type: ignore[arg-type]
            if retrieved:
                date.fromisoformat(retrieved)
        except ValueError:
            raise NutritionSupplementInvalid(f'entry {number}: date') from None
        target = values if kind == 'values' else fills
        if food_id in target:
            raise NutritionSupplementInvalid(f'entry {number}: duplicate {kind} for {food_id}')
        target[food_id] = NutritionSupplement(
            food_id, _text(entry.get('name')) or '', kind, str(state) if kind == 'values' else '', parsed, provider,
            source_name or '', _text(entry.get('source_record_id')) or '', url, retrieved, basis or '',
            approver or '', approved_on or '', version)
    return NutritionSupplements(values, fills)


def load_nutrition_supplements(path: Path | None = None) -> NutritionSupplements:
    path = path or DEFAULT_PATH
    if not path.exists():
        return NutritionSupplements({}, {})
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise NutritionSupplementInvalid('unreadable') from None
    return parse_nutrition_supplements(data)
