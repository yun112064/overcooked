"""每人這一餐吃到多少，對照每人每日建議攝取量（畫面用，不參與驗證與採用資格）。

取自 t11-t20-demo 的 per_person_nutrition.py。每份（用餐或便當）分到的量由 T12 依當餐熱量目標計算
（nutrition_planning DatabaseMenuResult.per_person）；這裡依成員合併：用餐是主要數字，便當另列，
不併入這一餐（2026-10-28 使用者決定：帶便當不應讓當餐營養變成兩倍）。
每日建議：熱量、蛋白質取自 T03 每人每日目標；其餘 5 項依 config/daily_reference_nutrients.json
由該成員每日熱量推算。
"""
from __future__ import annotations

import json
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from app.core.config import PROJECT_ROOT

REFERENCE_PATH = PROJECT_ROOT / "config" / "daily_reference_nutrients.json"


@lru_cache(maxsize=2)
def load_reference(path: str = str(REFERENCE_PATH)) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _decimal(value: str) -> Decimal:
    exact = Fraction(value)
    return Decimal(exact.numerator) / Decimal(exact.denominator)


def daily_reference(energy_kcal: Decimal, protein_g: Decimal, reference: dict | None = None) -> dict:
    """{營養素: {kind: target|range|max, value|min/max, label}}；值皆為字串。"""
    reference = reference or load_reference()
    out = {"energy_kcal": {"kind": "target", "value": str(energy_kcal), "label": "T03 每日熱量目標"},
           "protein_g": {"kind": "target", "value": str(protein_g), "label": "T03 每日蛋白質目標"}}
    for key, rule in reference["nutrients"].items():
        row = {"kind": rule["kind"], "label": rule["label"]}
        if rule.get("caveat"):
            row["caveat"] = rule["caveat"]
        per_g = Decimal(rule["kcal_per_g"]) if "kcal_per_g" in rule else None
        if "value" in rule:
            row["value" if rule["kind"] == "target" else "max"] = rule["value"]
        elif "per_1000_kcal" in rule:
            row["value"] = str(energy_kcal / 1000 * Decimal(rule["per_1000_kcal"]))
        else:
            if per_g is None:
                raise ValueError(f"{key}: energy-percent rule needs kcal_per_g")
            if "energy_percent_min" in rule:
                row["min"] = str(energy_kcal * Decimal(rule["energy_percent_min"]) / 100 / per_g)
            row["max"] = str(energy_kcal * Decimal(rule["energy_percent_max"]) / 100 / per_g)
        out[key] = row
    return out


def _portion(part: dict) -> dict:
    """畫面用的一份：份量、這一份應占每日比例、七種營養素攝取量與缺值時的已知部分。"""
    intake = dict(part.get("nutrition_all") or {})
    # 熱量、蛋白質用驗證過的精確值（與 nutrition_all 相同來源）
    intake.update({key: value for key, value in (part.get("nutrition") or {}).items() if value is not None})
    return {"participation_id": part["participation_id"], "n": part["n"], "share": part["share"],
            "meal": part["meal"], "meal_ratio": part["meal_ratio"], "dish_share": part["dish_share"],
            "dish_ids": part["dish_ids"], "staple_g": part["staple_g"], "intake": intake,
            "known_partial": dict(part.get("nutrition_known_partial") or {})}


def people(per_participation: list[dict], names: dict[str, str | None]) -> list[dict]:
    """依成員合併 T12 每份結果：dining 為這一餐，bento 另列；附每人每日建議。"""
    grouped: dict[str, dict] = {}
    for part in per_participation:
        person = grouped.setdefault(part["member_id"], {
            "member_id": part["member_id"], "name": names.get(part["member_id"]), "kinds": [],
            "dining": None, "bento": None,
            "daily": daily_reference(_decimal(part["daily"]["energy_kcal"]), _decimal(part["daily"]["protein_g"]))})
        kind = "bento" if part["kind"] == "bento" else "dining"
        person[kind] = _portion(part)
        if part["kind"] not in person["kinds"]:
            person["kinds"].append(part["kind"])
    return list(grouped.values())


def reference_meta() -> dict:
    ref = load_reference()
    return {"version": ref["version"], "source": ref["source"]}
