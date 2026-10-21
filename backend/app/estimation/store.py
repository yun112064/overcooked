"""換算係數快取（decisions #19）、TFDA 對照快取（decisions #28，kind=tfda_mapping）與 TFDA 營養缺值推估
（decisions #32／#45，kind=nutrient_fill，7 種營養素，鍵為 TFDA 樣本 ID＋營養素）：寫入、覆核與查表。

取用優先順序（同一 kind + food_id + key）：
1. TFDA／USDA 實測：不入此表，T12 計算時永遠先用（見 meal_calculation.make_tfda_calculator）；
2. 人工核可（approved）或人工覆寫（overridden）；
3. 未覆核的 LLM 推估（llm_estimated）；
同層取最新一列。rejected 永不使用。數值一律 Decimal。
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ConversionFactorEstimate
from app.meal_calc.types import EstimatedFactor

KINDS = ("cooking", "unit", "tfda_mapping", "nutrient_fill")
HUMAN_REVIEWED = ("approved", "overridden")
REVIEW_ACTIONS = {"approve": "approved", "reject": "rejected"}
# decisions #32／#45：缺值營養推估（kind=nutrient_fill）涵蓋全部 7 種營養素；每 100 g 的合理範圍（含邊界）
FILL_NUTRIENTS = ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g", "sugars_g", "dietary_fiber_g", "sodium_mg")
GRAM_RANGE = (Decimal(0), Decimal(100))
FILL_RANGE = {"energy_kcal": (Decimal(0), Decimal(900)), "protein_g": GRAM_RANGE, "carbohydrate_g": GRAM_RANGE,
              "fat_g": GRAM_RANGE, "sugars_g": GRAM_RANGE, "dietary_fiber_g": GRAM_RANGE,
              "sodium_mg": (Decimal(0), Decimal(40000))}
FILL_MAX = max(high for _, high in FILL_RANGE.values())
KCAL_PER_PROTEIN_G = Decimal(4)
KCAL_PER_FAT_G = Decimal(9)
# 碳水＋蛋白質＋脂肪＋纖維（每 100 g）可略超過 100 g（TFDA 碳水常含纖維、各項分析誤差）
MACRO_SUM_MAX = Decimal(105)
SUGAR_OVER_CARB_TOLERANCE = Decimal(1)


def _num(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def check_fill(sample_id: str, nutrient: str, value: Decimal) -> str | None:
    """nutrient_fill 值的檢查；回傳失敗原因（None 代表可用）。LLM 批次與人工覆寫共用。

    - 樣本必須存在、營養素必須是 7 種之一、TFDA 該欄位必須空白（TFDA 有值一律優先），
      且不適用「類別必然為 0」規則（decisions #45，那些營養素已是已知 0，不需推估）；
    - 數值在合理範圍內（每 100 g：熱量 0–900 kcal、各克數 0–100 g、鈉 0–40000 mg），並與樣本已知值一致：
      糖 ≤ 碳水（容許 1 g）、碳水＋蛋白質＋脂肪＋纖維 ≤ 105 g、蛋白質 4 kcal／g＋脂肪 9 kcal／g 不超過總熱量。
    """
    from app.services import nutrient_category_zero, tfda_mapping

    record = tfda_mapping.sample(sample_id)
    if record is None:
        return f"unknown TFDA sample id: {sample_id}"
    if nutrient not in FILL_NUTRIENTS:
        return f"nutrient_fill only covers {FILL_NUTRIENTS}"
    known = record.get("nutrition_per_100g") or {}
    if known.get(nutrient) is not None:
        return f"TFDA {sample_id} already has {nutrient}; TFDA wins"
    rule = nutrient_category_zero.applies(record, nutrient)
    if rule is not None:
        return f"{nutrient} of {sample_id} is a category zero ({rule['id']}); no estimate needed"
    low, high = FILL_RANGE[nutrient]
    if not low <= value <= high:
        return "OUT_OF_RANGE"
    values = {k: _num(known.get(k)) for k in FILL_NUTRIENTS}
    values[nutrient] = value
    sugars, carbs = values["sugars_g"], values["carbohydrate_g"]
    if sugars is not None and carbs is not None and sugars > carbs + SUGAR_OVER_CARB_TOLERANCE:
        return "OUT_OF_RANGE"
    if nutrient in ("carbohydrate_g", "protein_g", "fat_g", "dietary_fiber_g"):
        mass = sum((values[k] or Decimal(0)) for k in ("carbohydrate_g", "protein_g", "fat_g", "dietary_fiber_g"))
        if mass > MACRO_SUM_MAX:
            return "OUT_OF_RANGE"
    energy = values["energy_kcal"]
    if energy is not None and nutrient in ("energy_kcal", "protein_g", "fat_g"):
        macro_kcal = ((values["protein_g"] or Decimal(0)) * KCAL_PER_PROTEIN_G
                      + (values["fat_g"] or Decimal(0)) * KCAL_PER_FAT_G)
        if macro_kcal > energy * Decimal("1.1") + 1:
            return "OUT_OF_RANGE"
    return None


def _key(kind: str, key: str) -> str:
    if kind == "cooking":
        from app.services.meal_calculation import normalize_cooking_method
        return normalize_cooking_method(key) or key
    return key.strip()


def add_estimate(db: Session, *, kind: str, food_id: str, key: str, source: str, status: str,
                 food_name: str | None = None, yield_factor: Decimal | None = None, retention: dict | None = None,
                 grams_per_unit: Decimal | None = None, model: str | None = None, prompt_version: str | None = None,
                 schema_version: str | None = None, input_snapshot: dict | None = None,
                 assumptions: list | None = None, call_key: str | None = None, reviewer: str | None = None,
                 review_note: str | None = None, tfda_sample_id: str | None = None,
                 nutrient_per_100g: Decimal | None = None) -> uuid.UUID:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    retention = retention or {}
    now = datetime.now(timezone.utc)
    row = ConversionFactorEstimate(
        id=uuid.uuid4(), kind=kind, food_id=food_id, factor_key=_key(kind, key), food_name=food_name,
        yield_factor=yield_factor, energy_retention=retention.get("energy_kcal"),
        protein_retention=retention.get("protein_g"), grams_per_unit=grams_per_unit, tfda_sample_id=tfda_sample_id,
        nutrient_per_100g=nutrient_per_100g, source=source, status=status,
        model=model, prompt_version=prompt_version, schema_version=schema_version,
        input_snapshot=input_snapshot or {}, assumptions=list(assumptions or []), call_key=call_key,
        reviewer=reviewer, review_note=review_note, reviewed_at=now if reviewer else None, created_at=now)
    db.add(row)
    db.flush()
    return row.id


def review(db: Session, estimate_id, *, action: str, reviewer: str, note: str | None) -> None:
    """approve／reject 一列（不改數值）；改數值請用 override。"""
    if action not in REVIEW_ACTIONS:
        raise ValueError(f"action must be one of {sorted(REVIEW_ACTIONS)}")
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewer is required")
    row = db.get(ConversionFactorEstimate, uuid.UUID(str(estimate_id)))
    if row is None:
        raise LookupError(str(estimate_id))
    row.status = REVIEW_ACTIONS[action]
    row.reviewer, row.review_note, row.reviewed_at = reviewer.strip(), note, datetime.now(timezone.utc)
    db.flush()


def override(db: Session, *, kind: str, food_id: str, key: str, reviewer: str, note: str | None,
             yield_factor: Decimal | None = None, retention: dict | None = None,
             grams_per_unit: Decimal | None = None, tfda_sample_id: str | None = None,
             nutrient_per_100g: Decimal | None = None) -> uuid.UUID:
    """人工覆寫：新增 source=human、status=overridden 的一列（舊列保留供追溯）。

    tfda_mapping 的覆寫值必須是存在的 TFDA 樣本 ID（decisions #28）。
    nutrient_fill（decisions #32）：food_id 為 TFDA 樣本 ID、key 為必要營養，值須通過 check_fill。
    """
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewer is required")
    if kind == "tfda_mapping":
        from app.services import tfda_mapping
        if tfda_mapping.lookup_name(tfda_sample_id or "") is None:
            raise ValueError(f"unknown TFDA sample id: {tfda_sample_id}")
    if kind == "nutrient_fill":
        if nutrient_per_100g is None:
            raise ValueError("nutrient_fill override requires a value")
        failure = check_fill(food_id, key.strip(), nutrient_per_100g)
        if failure:
            raise ValueError(failure)
    return add_estimate(db, kind=kind, food_id=food_id, key=key, source="human", status="overridden",
                        yield_factor=yield_factor, retention=retention, grams_per_unit=grams_per_unit,
                        tfda_sample_id=tfda_sample_id, nutrient_per_100g=nutrient_per_100g,
                        reviewer=reviewer.strip(), review_note=note)


def _factor(row: ConversionFactorEstimate) -> EstimatedFactor:
    retention = None
    if row.kind == "cooking":
        retention = {"energy_kcal": Decimal(row.energy_retention), "protein_g": Decimal(row.protein_retention)}
    return EstimatedFactor(
        estimate_id=str(row.id), kind=row.kind, food_id=row.food_id, key=row.factor_key, source=row.source,
        status=row.status, model=row.model, prompt_version=row.prompt_version, schema_version=row.schema_version,
        yield_factor=Decimal(row.yield_factor) if row.yield_factor is not None else None, retention=retention,
        grams_per_unit=Decimal(row.grams_per_unit) if row.grams_per_unit is not None else None,
        tfda_sample_id=row.tfda_sample_id,
        nutrient_per_100g=Decimal(row.nutrient_per_100g) if row.nutrient_per_100g is not None else None)


def _rank(row: ConversionFactorEstimate):
    return (0 if row.status in HUMAN_REVIEWED else 1, -row.created_at.timestamp())


@dataclass
class FactorSnapshot:
    """一次計算使用的快取快照（整表載入：列數只與食材×方法／單位組合數有關）。"""

    factors: dict = field(default_factory=dict)

    def cooking(self, food_id: str, method: str | None) -> EstimatedFactor | None:
        return self.factors.get(("cooking", food_id, _key("cooking", method))) if method else None

    def unit(self, food_id: str, unit: str | None) -> EstimatedFactor | None:
        return self.factors.get(("unit", food_id, unit.strip())) if unit else None

    def nutrient_fill(self, sample_id: str | None, nutrient: str) -> EstimatedFactor | None:
        """decisions #32：TFDA 樣本某必要營養的每 100 g 推估（人工優先）。"""
        return self.factors.get(("nutrient_fill", sample_id, nutrient)) if sample_id else None

    def mappings(self) -> dict[str, EstimatedFactor]:
        """decisions #28：food_id → 最佳的 TFDA 對照列（人工優先；可能是「無合適樣本」）。"""
        return {food_id: f for (kind, food_id, _), f in self.factors.items() if kind == "tfda_mapping"}


def load_snapshot(db: Session) -> FactorSnapshot:
    rows = db.scalars(select(ConversionFactorEstimate).where(ConversionFactorEstimate.status != "rejected")).all()
    best: dict = {}
    for row in rows:
        k = (row.kind, row.food_id, row.factor_key)
        if k not in best or _rank(row) < _rank(best[k]):
            best[k] = row
    return FactorSnapshot({k: _factor(v) for k, v in best.items()})


def existing_keys(db: Session) -> set[tuple[str, str, str]]:
    """已有任何紀錄（含 rejected）的鍵：批次預設跳過，避免重複付費或推翻人工的駁回。"""
    rows = db.execute(select(ConversionFactorEstimate.kind, ConversionFactorEstimate.food_id,
                             ConversionFactorEstimate.factor_key).distinct()).all()
    return {tuple(r) for r in rows}


def list_estimates(db: Session, *, kind: str | None = None, status: str | None = None,
                   food_id: str | None = None) -> list[dict]:
    q = select(ConversionFactorEstimate).order_by(ConversionFactorEstimate.kind, ConversionFactorEstimate.food_id,
                                                  ConversionFactorEstimate.factor_key,
                                                  ConversionFactorEstimate.created_at)
    if kind:
        q = q.where(ConversionFactorEstimate.kind == kind)
    if status:
        q = q.where(ConversionFactorEstimate.status == status)
    if food_id:
        q = q.where(ConversionFactorEstimate.food_id == food_id)
    return [{
        "id": str(r.id), "kind": r.kind, "food_id": r.food_id, "food_name": r.food_name, "key": r.factor_key,
        "yield_factor": r.yield_factor, "energy_retention": r.energy_retention,
        "protein_retention": r.protein_retention, "grams_per_unit": r.grams_per_unit,
        "tfda_sample_id": r.tfda_sample_id, "nutrient_per_100g": r.nutrient_per_100g, "source": r.source,
        "status": r.status, "model": r.model, "prompt_version": r.prompt_version,
        "schema_version": r.schema_version, "assumptions": r.assumptions, "reviewer": r.reviewer,
        "review_note": r.review_note, "created_at": r.created_at,
    } for r in db.scalars(q).all()]
