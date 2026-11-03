"""Display-only nutrient gaps, filled the t11-t20-demo way (user decision 2026-11-03).

Only the five nutrients shown next to energy and protein use this; the ±10% check
and adoption never do.  A blank TFDA cell is filled, in order, by:

1. ``config/nutrient_category_zero.json``: conservative "must be 0" rules by TFDA
   sample category (e.g. pure oil has no carbohydrate, meat has no fibre);
2. ``config/t12_nutrient_fills.json``: cached per-sample estimates
   (``scripts/estimate_nutrient_fills.py``, gpt-5.4-mini, unreviewed).

A TFDA value always wins.  Every fill is recorded on the record's source as
``display_fills`` so the screen can stay unlabelled while the trace keeps it.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZERO_PATH = ROOT / 'config' / 'nutrient_category_zero.json'
FILLS_PATH = ROOT / 'config' / 't12_nutrient_fills.json'


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.is_file() else 0.0


def _sources():
    """Rules and estimates; re-read when either file changes (new estimates apply without a restart)."""
    return _load(str(ZERO_PATH), str(FILLS_PATH), _mtime(ZERO_PATH), _mtime(FILLS_PATH))


@lru_cache(maxsize=2)
def _load(zero_path: str, fills_path: str, _zero_mtime: float, _fills_mtime: float):
    def read(path: str) -> dict:
        p = Path(path)
        return json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}
    rules = read(zero_path).get('rules') or []
    fills = {(f['sample_id'], f['nutrient']): f for f in read(fills_path).get('fills') or []}
    return tuple(rules), fills


def zero_rule(sample_id: str, nutrient: str) -> str | None:
    """The id of the category rule that makes this nutrient a known 0, if any."""
    rules, _ = _sources()
    for rule in rules:
        if nutrient in rule.get('nutrients', ()) and any(sample_id.startswith(p) for p in rule.get('prefixes', ())):
            return str(rule.get('id'))
    return None


def estimate(sample_id: str, nutrient: str) -> Mapping | None:
    _, fills = _sources()
    return fills.get((sample_id, nutrient))


def fill_record(record: Mapping) -> Mapping:
    """Copy of a T12 line record with blank display-only nutrients filled and traced."""
    values = dict(record.get('nutrition_per_100g') or {})
    sample_id = str((record.get('source') or {}).get('source_id') or '')
    filled = []
    for nutrient in ('fat_g', 'carbohydrate_g', 'sugars_g', 'sodium_mg', 'dietary_fiber_g'):
        if values.get(nutrient) is not None or not sample_id:
            continue
        rule = zero_rule(sample_id, nutrient)
        if rule is not None:
            values[nutrient] = Fraction(0)
            filled.append({'nutrient': nutrient, 'basis': 'category_zero', 'rule': rule})
            continue
        found = estimate(sample_id, nutrient)
        if found is not None:
            values[nutrient] = Fraction(str(found['value_per_100g']))
            filled.append({'nutrient': nutrient, 'basis': 'llm_estimated', 'model': found.get('model'),
                           'prompt_version': found.get('prompt_version'), 'origin': found.get('origin')})
    if not filled:
        return record
    out = deepcopy(dict(record))
    out['nutrition_per_100g'] = values
    out['source'] = {**dict(record.get('source') or {}), 'display_fills': filled}
    return out
