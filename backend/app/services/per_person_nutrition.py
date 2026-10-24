"""每人這一餐吃到多少，對照每人每日建議攝取量（畫面用，不參與驗證與採用資格）。

整餐是依食量 n 縮放而成，所以某成員吃到的量 = 整餐營養 × 該成員 n ÷ 全家總 n；同一人同時用餐與帶便當時合併。
每日建議：熱量、蛋白質取自 T03 每人每日目標（結果快照的 targets.items：scaled_target ÷ (meal_ratio × n)，
與 T03 daily_reference 相同）；其餘 5 項依 config/daily_reference_nutrients.json 由該成員每日熱量推算。
"""
from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.core.config import PROJECT_ROOT

REFERENCE_PATH = PROJECT_ROOT / "config" / "daily_reference_nutrients.json"
OTHER_KEYS = ("carbohydrate_g", "fat_g", "sugars_g", "sodium_mg", "dietary_fiber_g")


@lru_cache(maxsize=2)
def load_reference(path: str = str(REFERENCE_PATH)) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


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
            if "energy_percent_min" in rule:
                row["min"] = str(energy_kcal * Decimal(rule["energy_percent_min"]) / 100 / per_g)
            row["max"] = str(energy_kcal * Decimal(rule["energy_percent_max"]) / 100 / per_g)
        out[key] = row
    return out


def per_person(payload: dict, nutrition: dict, member_names: dict[str, str | None]) -> list[dict]:
    """依成員合併的每人攝取量與每日建議。nutrition 是 meal_results._nutrition 的整餐結果（含已知部分）。
    目標或份數缺漏時回空清單（畫面改顯示整餐）。"""
    targets = payload.get("targets") or {}
    total_n = _dec(nutrition.get("total_n") or targets.get("total_n"))
    items = targets.get("items") or []
    if not items or not total_n:
        return []
    others = nutrition.get("other_nutrients") or {}
    whole = {"energy_kcal": _dec(nutrition.get("energy_kcal")), "protein_g": _dec(nutrition.get("protein_g")),
             **{key: _dec(others.get(key)) for key in OTHER_KEYS}}
    known = {key: _dec(v) for key, v in (nutrition.get("known_partial") or {}).items()}

    people: dict[str, dict] = {}
    for item in items:
        member_id = item["participation_id"].split(":", 1)[0]
        n = Decimal(str(item["n"]))
        scaled = item.get("scaled_target") or {}
        per_day = Decimal(str(item["meal_ratio"])) * n
        person = people.setdefault(member_id, {
            "member_id": member_id, "name": member_names.get(member_id), "kinds": [], "n": Decimal(0),
            "meal_ratio": Decimal(0),
            "daily_energy": Decimal(str(scaled["energy_kcal"])) / per_day,
            "daily_protein": Decimal(str(scaled["protein_g"])) / per_day,
        })
        person["n"] += n
        # 用餐＋便當是兩餐：建議占比相加（例如晚餐 40%＋午餐便當 30%）
        person["meal_ratio"] += Decimal(str(item["meal_ratio"]))
        if item["kind"] not in person["kinds"]:
            person["kinds"].append(item["kind"])

    out = []
    for p in people.values():
        share = p["n"] / total_n
        intake = {key: None if value is None else str(value * share) for key, value in whole.items()}
        partial = {key: str(known[key] * share) for key, value in whole.items()
                   if value is None and known.get(key) is not None}
        out.append({"member_id": p["member_id"], "name": p["name"], "kinds": p["kinds"], "n": str(p["n"]),
                    "share": str(share), "meal_ratio": str(p["meal_ratio"]), "intake": intake, "known_partial": partial,
                    "daily": daily_reference(p["daily_energy"], p["daily_protein"])})
    return out


def reference_meta() -> dict:
    ref = load_reference()
    return {"version": ref["version"], "source": ref["source"],
            "high_sodium_dish_mg": ref["high_sodium_dish"]["mg_per_serving"]}
