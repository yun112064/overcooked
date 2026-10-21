"""食譜食材 → TFDA 樣本對照（decisions #28）。

T04 nutrition_model 只以名稱查 TFDA（find_ingredient）；食譜用語（空心菜、青蔥、食鹽、辣椒）常對不到
TFDA 名稱（蕹菜、青蔥平均值……）或對到多筆無法唯一判定。解析順序：

1. exact：食譜名稱本身由 T04 解得到（T04 內建的人工對照、正式名稱、唯一別名、唯一平均值）；
2. alias：工作簿 ingredient_aliases 的別名（不含歧義別名）由 T04 解得到；
3. synonym：已確認同義詞群組（config/ingredient_synonyms.json，status=confirmed）其他成員的名稱；
4. human：對照快取（conversion_factor_estimates，kind=tfda_mapping）中人工核可（approved）或覆寫
   （overridden）的樣本；
5. llm_mapped：對照快取中未覆核的 gpt-5.4-mini 批次推薦（python -m app.estimation run --kind tfda_mapping）；
6. 都沒有 → None（calculator 維持 T04 的 ingredient_not_found，不填 0）。

rejected 永不使用；模型回答「無合適樣本」的紀錄（tfda_sample_id 為 None）只用來讓批次冪等。
對照只決定「用哪一筆 TFDA 樣本」，營養仍由 Python 以 Decimal 計算（T04 main 設計）。
"""
import difflib
from dataclasses import dataclass, field
from functools import lru_cache

from app.meal_calc.types import EstimatedFactor

KIND = "tfda_mapping"
MAPPING_KEY = "tfda"
MAX_CANDIDATES = 15
MIN_SCORE = 0.5
HUMAN_STATUSES = ("approved", "overridden")
LLM_MAPPING_LABEL = "LLM 對照"


def _service():
    from app.services.meal_calculation import _nutrition_model
    return _nutrition_model()


@lru_cache(maxsize=1)
def _by_id() -> dict:
    return {r["ingredient_id"]: r for r in _service().ingredients}


def sample(sample_id: str | None) -> dict | None:
    """TFDA 樣本紀錄（T04 ingredient_master）；不存在回傳 None。"""
    return _by_id().get(sample_id) if sample_id else None


def lookup_name(sample_id: str) -> str | None:
    """T04 以名稱查表：找一個由 find_ingredient 解回同一樣本的名稱（正式名稱優先，其次別名）。"""
    record = sample(sample_id)
    if record is None:
        return None
    service = _service()
    for name in [record["standard_name"], *record.get("aliases", [])]:
        found = service.find_ingredient(name)
        if found and found["ingredient_id"] == sample_id:
            return name
    return None


@dataclass(frozen=True)
class TfdaResolution:
    source: str              # exact／alias／synonym／human／llm_mapped
    sample_id: str
    tfda_name: str           # 交給 T04 查表的名稱（解回 sample_id）
    input_name: str          # 食譜食材名稱
    via_name: str | None = None
    estimate: EstimatedFactor | None = None

    @property
    def llm_mapped(self) -> bool:
        return self.source == "llm_mapped"

    def trace(self) -> dict:
        return {"source": self.source, "sample_id": self.sample_id, "tfda_name": self.tfda_name,
                "input_name": self.input_name, "via_name": self.via_name, "llm_mapped": self.llm_mapped,
                "estimate": self.estimate.marker() if self.estimate else None}


@dataclass
class TfdaResolver:
    """aliases／synonyms：food_id → 候選名稱；mappings：food_id → 快取最佳一列（store.load_snapshot 已排序）。"""

    aliases: dict = field(default_factory=dict)
    synonyms: dict = field(default_factory=dict)
    mappings: dict = field(default_factory=dict)

    def _by_name(self, food_id, name) -> TfdaResolution | None:
        service = _service()
        found = service.find_ingredient(name) if name else None
        if found:
            return TfdaResolution("exact", found["ingredient_id"], name, name)
        for source, table in (("alias", self.aliases), ("synonym", self.synonyms)):
            for alt in table.get(food_id) or ():
                if not alt or alt == name:
                    continue
                found = service.find_ingredient(alt)
                if found:
                    return TfdaResolution(source, found["ingredient_id"], alt, name, via_name=alt)
        return None

    def resolve_by_name(self, food_id: str, name: str) -> TfdaResolution | None:
        """只用名稱（精確、別名、同義詞）解析；批次以此判斷是否需要對照。"""
        return self._by_name(food_id, name)

    def resolve(self, food_id: str, name: str) -> TfdaResolution | None:
        res = self._by_name(food_id, name)
        if res is not None:
            return res
        factor = self.mappings.get(food_id)
        if factor is None or not factor.usable or not factor.tfda_sample_id:
            return None
        tfda_name = lookup_name(factor.tfda_sample_id)
        if tfda_name is None:
            return None
        human = factor.source == "human" or factor.status in HUMAN_STATUSES
        return TfdaResolution("human" if human else "llm_mapped", factor.tfda_sample_id, tfda_name, name,
                              estimate=factor)


EXACT_ONLY = TfdaResolver()


def alias_tables(db) -> tuple[dict, dict, dict]:
    """(aliases, synonyms, canonical)：工作簿別名（去掉歧義別名）、已確認同義詞群組其他成員的名稱。"""
    from sqlalchemy import select

    from app.models import IngredientAlias
    from app.services import synonyms as synonym_service

    rows = db.execute(select(IngredientAlias.food_id, IngredientAlias.canonical_name, IngredientAlias.aliases,
                             IngredientAlias.ambiguous_aliases)).all()
    canonical = {r.food_id: r.canonical_name for r in rows}
    aliases = {}
    for r in rows:
        ambiguous = set(r.ambiguous_aliases or [])
        aliases[r.food_id] = [a for a in (r.aliases or []) if a and a not in ambiguous]
    synonyms: dict = {}
    for group in synonym_service.confirmed_groups():
        for food_id in group:
            synonyms[food_id] = sorted(canonical[o] for o in group - {food_id} if o in canonical)
    return aliases, synonyms, canonical


def load_resolver(db, snapshot=None) -> TfdaResolver:
    from app.estimation.store import load_snapshot

    aliases, synonyms, _ = alias_tables(db)
    snapshot = snapshot if snapshot is not None else load_snapshot(db)
    return TfdaResolver(aliases=aliases, synonyms=synonyms, mappings=snapshot.mappings())


def _score(query: str, target: str) -> float:
    if not query or not target:
        return 0.0
    if query == target:
        return 1.0
    if query in target or target in query:
        return 0.9
    return difflib.SequenceMatcher(None, query, target).ratio()


def candidates(names: list[str], limit: int = MAX_CANDIDATES) -> list[dict]:
    """以字串相似度（完全相同 > 包含 > 序列相似度）從 TFDA 樣本中縮小候選；第一個名稱（食譜名稱）權重最高。

    模型只能從這份清單選（或回答 NONE），因此清單外的樣本永遠不會被對照。
    """
    queries = [(n.strip(), 1.0 if i == 0 else 0.9) for i, n in enumerate(names) if n and n.strip()]
    scored = []
    for record in _service().ingredients:
        targets = [record["standard_name"], *record.get("aliases", [])]
        score = max((_score(q, t) * w for q, w in queries for t in targets), default=0.0)
        if score >= MIN_SCORE:
            scored.append((-score, len(record["standard_name"]), record["ingredient_id"], record))
    scored.sort(key=lambda x: x[:3])
    out = []
    for neg, _, _, record in scored[:limit]:
        values = record.get("nutrition_per_100g") or {}
        out.append({"sample_id": record["ingredient_id"], "name": record["standard_name"],
                    "aliases": list(record.get("aliases", []))[:6], "state": record.get("state"),
                    "description": record.get("description"),
                    "energy_kcal_per_100g": values.get("energy_kcal"), "protein_g_per_100g": values.get("protein_g"),
                    "similarity": round(-neg, 3)})
    return out
