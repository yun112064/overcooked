"""換算係數快取（decisions #19）與 TFDA 對照快取（decisions #28，kind=tfda_mapping）：寫入、覆核與查表。

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

KINDS = ("cooking", "unit", "tfda_mapping")
HUMAN_REVIEWED = ("approved", "overridden")
REVIEW_ACTIONS = {"approve": "approved", "reject": "rejected"}


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
                 review_note: str | None = None, tfda_sample_id: str | None = None) -> uuid.UUID:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    retention = retention or {}
    now = datetime.now(timezone.utc)
    row = ConversionFactorEstimate(
        id=uuid.uuid4(), kind=kind, food_id=food_id, factor_key=_key(kind, key), food_name=food_name,
        yield_factor=yield_factor, energy_retention=retention.get("energy_kcal"),
        protein_retention=retention.get("protein_g"), grams_per_unit=grams_per_unit, tfda_sample_id=tfda_sample_id,
        source=source, status=status,
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
             grams_per_unit: Decimal | None = None, tfda_sample_id: str | None = None) -> uuid.UUID:
    """人工覆寫：新增 source=human、status=overridden 的一列（舊列保留供追溯）。

    tfda_mapping 的覆寫值必須是存在的 TFDA 樣本 ID（decisions #28）。
    """
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewer is required")
    if kind == "tfda_mapping":
        from app.services import tfda_mapping
        if tfda_mapping.lookup_name(tfda_sample_id or "") is None:
            raise ValueError(f"unknown TFDA sample id: {tfda_sample_id}")
    return add_estimate(db, kind=kind, food_id=food_id, key=key, source="human", status="overridden",
                        yield_factor=yield_factor, retention=retention, grams_per_unit=grams_per_unit,
                        tfda_sample_id=tfda_sample_id, reviewer=reviewer.strip(), review_note=note)


def _factor(row: ConversionFactorEstimate) -> EstimatedFactor:
    retention = None
    if row.kind == "cooking":
        retention = {"energy_kcal": Decimal(row.energy_retention), "protein_g": Decimal(row.protein_retention)}
    return EstimatedFactor(
        estimate_id=str(row.id), kind=row.kind, food_id=row.food_id, key=row.factor_key, source=row.source,
        status=row.status, model=row.model, prompt_version=row.prompt_version, schema_version=row.schema_version,
        yield_factor=Decimal(row.yield_factor) if row.yield_factor is not None else None, retention=retention,
        grams_per_unit=Decimal(row.grams_per_unit) if row.grams_per_unit is not None else None,
        tfda_sample_id=row.tfda_sample_id)


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
        "tfda_sample_id": r.tfda_sample_id, "source": r.source,
        "status": r.status, "model": r.model, "prompt_version": r.prompt_version,
        "schema_version": r.schema_version, "assumptions": r.assumptions, "reviewer": r.reviewer,
        "review_note": r.review_note, "created_at": r.created_at,
    } for r in db.scalars(q).all()]
