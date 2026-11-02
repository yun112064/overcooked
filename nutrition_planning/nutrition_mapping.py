"""T12-only nutrition source mapping (user decision 2026-11-01, option 1).

A recipe keeps its own Food_ID everywhere (T07 avoid/allergen lists, T11
knowledge, T13 prices).  Only T12 nutrition lookup may follow an approved
entry here, e.g. recipe 薑 FOOD001496 takes its nutrition from TFDA 老薑
FOOD000359, or 高麗菜 FOOD000867 uses the TFDA sample E30001 (甘藍平均值).

Only entries with ``status == "approved"`` and a named approver are applied.
A malformed file fails closed: T12 reports it instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'config' / 't12_nutrition_mappings.json'
SCHEMA_VERSION = 't12-nutrition-mapping-v1'
FOOD_ID = re.compile(r'FOOD\d{6}')
SOURCE_RECORD = re.compile(r'[A-Za-z0-9_.-]{1,40}')


class NutritionMappingInvalid(ValueError):
    """The mapping file cannot be trusted; callers report, never guess."""


@dataclass(frozen=True)
class NutritionMapping:
    food_id: str
    nutrition_food_id: str
    source_record_id: str | None
    name: str
    nutrition_name: str
    basis: str
    approved_by: str
    approved_on: str
    mapping_version: str

    @property
    def provenance(self) -> dict:
        return {'mapping_version': self.mapping_version, 'recipe_food_id': self.food_id,
                'nutrition_food_id': self.nutrition_food_id, 'source_record_id': self.source_record_id,
                'name': self.name, 'nutrition_name': self.nutrition_name, 'basis': self.basis,
                'approved_by': self.approved_by, 'approved_on': self.approved_on}


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_nutrition_mappings(data) -> dict[str, NutritionMapping]:
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION:
        raise NutritionMappingInvalid('schema_version')
    version = _text(data.get('version'))
    entries = data.get('mappings')
    if version is None or not isinstance(entries, list):
        raise NutritionMappingInvalid('version or mappings')
    result: dict[str, NutritionMapping] = {}
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise NutritionMappingInvalid(f'entry {number}')
        status = entry.get('status')
        if status not in ('approved', 'pending', 'rejected'):
            raise NutritionMappingInvalid(f'entry {number}: status')
        food_id = _text(entry.get('food_id'))
        target = _text(entry.get('nutrition_food_id')) or food_id
        record = _text(entry.get('source_record_id'))
        if food_id is None or not FOOD_ID.fullmatch(food_id) or target is None or not FOOD_ID.fullmatch(target):
            raise NutritionMappingInvalid(f'entry {number}: Food_ID')
        if record is not None and not SOURCE_RECORD.fullmatch(record):
            raise NutritionMappingInvalid(f'entry {number}: source_record_id')
        if target == food_id and record is None:
            raise NutritionMappingInvalid(f'entry {number}: maps a food to itself without a source')
        if status != 'approved':
            continue
        approver, approved_on = _text(entry.get('approved_by')), _text(entry.get('approved_on'))
        basis = _text(entry.get('basis'))
        if approver is None or approved_on is None or basis is None:
            raise NutritionMappingInvalid(f'entry {number}: approval record')
        try:
            date.fromisoformat(approved_on)
        except ValueError:
            raise NutritionMappingInvalid(f'entry {number}: approved_on') from None
        if food_id in result:
            raise NutritionMappingInvalid(f'entry {number}: duplicate {food_id}')
        result[food_id] = NutritionMapping(food_id, target, record, _text(entry.get('name')) or '',
                                           _text(entry.get('nutrition_name')) or '', basis, approver,
                                           approved_on, version)
    return result


def load_nutrition_mappings(path: Path | None = None) -> dict[str, NutritionMapping]:
    path = path or DEFAULT_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise NutritionMappingInvalid('unreadable') from None
    return parse_nutrition_mappings(data)
