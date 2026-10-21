"""T12 → T13 共用介面。欄位須與 T12 完全一致，勿自行增減。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class IngredientRequirement:
    """一項配方食材的整餐需求量：已乘 N 的生重克數，未取整。"""

    food_id: str
    grams: Decimal


@dataclass(frozen=True)
class EstimatedFactor:
    """快取的換算係數（decisions #19）：烹調（產率／保留率）或每單位克數。

    kind="cooking" 時 key 為正規化的烹調方式；kind="unit" 時 key 為原單位（如「匙」）；
    kind="tfda_mapping"（decisions #28）時 key 固定為 "tfda"，值為 tfda_sample_id。
    source：tfda／human／llm_estimated；status：llm_estimated／approved／rejected／overridden。
    rejected 永不使用。數值一律 Decimal，未取整。
    """

    estimate_id: str
    kind: str
    food_id: str
    key: str
    source: str
    status: str
    model: str | None
    prompt_version: str | None
    schema_version: str | None
    yield_factor: Decimal | None = None
    retention: dict | None = None
    grams_per_unit: Decimal | None = None
    # kind="tfda_mapping"（decisions #28）：對照到的 TFDA 樣本 ID；None 代表模型回答「無合適樣本」
    tfda_sample_id: str | None = None

    @property
    def usable(self) -> bool:
        return self.status != "rejected"

    @property
    def llm_estimated(self) -> bool:
        """結果標示「LLM 推估」：數值來自 LLM（含人工核可但未改值者）；人工覆寫值不算。"""
        return self.source == "llm_estimated"

    def marker(self) -> dict:
        out = {"kind": self.kind, "estimate_id": self.estimate_id, "food_id": self.food_id, "key": self.key,
               "source": self.source, "status": self.status, "model": self.model,
               "prompt_version": self.prompt_version, "schema_version": self.schema_version,
               "llm_estimated": self.llm_estimated}
        if self.kind == "cooking":
            out.update(yield_factor=self.yield_factor, retention=dict(self.retention or {}))
        elif self.kind == "tfda_mapping":
            out["tfda_sample_id"] = self.tfda_sample_id
        else:
            out["grams_per_unit"] = self.grams_per_unit
        return out
