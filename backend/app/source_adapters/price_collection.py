"""One resilient price collection run: PX Mart first, then an optional fallback source.

決策 #25：全聯查無或不可用（selection_tier=missing，不含 review_required 與工作簿對應失敗）的
食材，才以第二來源（萬家福）查價。第二來源用獨立的 run（run_key 加 ":<provider>"），
統計分來源記錄；同一 run_key 重跑可續跑、已完成則略過（冪等）。
"""

from __future__ import annotations

import json

import time
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable
from urllib.parse import quote as url_quote

from app.core.logging_config import scheduler_logger

from .ingredient_workbook import load_ingredient_queries
from .price_models import (
    IngredientQuery,
    PriceQuote,
    QuoteStatus,
    SelectionTier,
)
from .pricing_repository import PricingRepository, RunProgress
from .product_matching import POLICY_VERSION
from .pxmart import PxMartAdapter, SEARCH_URL


@dataclass(frozen=True)
class CollectionResult:
    run_key: str
    status: str
    ingredient_total: int = 0
    success_count: int = 0
    review_count: int = 0
    failure_count: int = 0
    auto_adopted_count: int = 0
    estimate_count: int = 0
    missing_count: int = 0
    checkpoint_count: int = 0
    mapping_failure_rate: float = 0.0
    price_update_success_rate: float = 0.0
    duration_ms: int = 0
    skipped_reason: str | None = None
    provider: str = "pxmart"
    fallback: "CollectionResult | None" = None


PRICE_EXEMPT_PATH = Path(__file__).resolve().parents[3] / "config/price_exempt_ingredients.json"


def load_price_exempt(path: str | Path = PRICE_EXEMPT_PATH) -> set[str]:
    """不查價的 FOOD 編號（例如水）；檔案不存在時視為沒有例外。"""
    path = Path(path)
    if not path.is_file():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8"))["ingredients"])


def merge_confirmed_synonyms(queries: list[IngredientQuery], groups: list[set[str]]) -> list[IngredientQuery]:
    """已確認是同一種食材的不同 Food_ID 只查一次價；其他名稱當作商品比對提示。"""
    merged = list(queries)
    for group in groups:
        members = [q for q in merged if set(q.food_ids) & group]
        if len(members) < 2:
            continue
        keep = members[0]
        aliases = list(keep.aliases)
        food_ids = list(keep.food_ids)
        for other in members[1:]:
            for name in (other.canonical_name, *other.aliases):
                if name not in aliases and name != keep.canonical_name:
                    aliases.append(name)
            food_ids += [i for i in other.food_ids if i not in food_ids]
        combined = keep.model_copy(update={"food_ids": food_ids, "aliases": aliases})
        merged = [combined if q is keep else q for q in merged if q is keep or q not in members]
    return merged


class PriceCollectionService:
    def __init__(
        self,
        *,
        workbook_path: str | Path,
        repository: PricingRepository,
        adapter_factory: Callable[[], PxMartAdapter],
        delay_seconds: float = 1.5,
        ingredient_loader: Callable[[str | Path], list[IngredientQuery]] = (
            load_ingredient_queries
        ),
        sleep: Callable[[float], None] = time.sleep,
        exempt_food_ids: set[str] | None = None,
        synonym_groups: list[set[str]] | None = None,
        fallback_adapter_factory: Callable[[], object] | None = None,
    ) -> None:
        self.fallback_adapter_factory = fallback_adapter_factory
        if synonym_groups is None:
            from app.services.synonyms import confirmed_groups
            synonym_groups = confirmed_groups()
        self.synonym_groups = synonym_groups
        self.exempt_food_ids = load_price_exempt() if exempt_food_ids is None else set(exempt_food_ids)
        self.workbook_path = Path(workbook_path)
        self.repository = repository
        self.adapter_factory = adapter_factory
        self.delay_seconds = delay_seconds
        self.ingredient_loader = ingredient_loader
        self.sleep = sleep

    def run(
        self,
        *,
        trigger_type: str,
        scheduled_for: datetime,
        run_key: str | None = None,
        limit: int | None = None,
        batch_size: int | None = None,
        food_ids: set[str] | None = None,
    ) -> CollectionResult:
        """food_ids：只查這些 FOOD 編號（T15 缺價定向查價）；None 為全部。"""
        if run_key is None:
            run_key = f"pxmart-manual-{uuid.uuid4()}"
        common = dict(trigger_type=trigger_type, scheduled_for=scheduled_for, batch_size=batch_size,
                      food_ids=food_ids)
        primary = self._collect(provider="pxmart", run_key=run_key, adapter_factory=self.adapter_factory,
                                limit=limit, **common)
        if self.fallback_adapter_factory is None:
            return primary
        fallback_provider = getattr(self.fallback_adapter_factory, "provider", "uniprosperity")
        fallback = self._collect(
            provider=fallback_provider, run_key=f"{run_key}:{fallback_provider}",
            adapter_factory=self.fallback_adapter_factory, limit=None,
            target_filter=lambda lease: self.repository.fallback_targets(lease, primary_run_key=run_key),
            **common)
        return replace(primary, fallback=fallback)

    def _collect(
        self,
        *,
        provider: str,
        run_key: str,
        adapter_factory: Callable[[], object],
        trigger_type: str,
        scheduled_for: datetime,
        limit: int | None,
        batch_size: int | None,
        food_ids: set[str] | None,
        target_filter: Callable[[object], tuple[set[str], bool]] | None = None,
    ) -> CollectionResult:
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        lease = self.repository.try_begin_run(
            run_key=run_key,
            provider=provider,
            trigger_type=trigger_type,
            scheduled_for=scheduled_for,
            started_at=started_at,
        )
        if lease is None:
            scheduler_logger.info(
                "price collection skipped",
                extra={"event": "price_collection_skipped"},
            )
            return CollectionResult(
                run_key=run_key,
                provider=provider,
                status="skipped",
                skipped_reason="run_key already exists or advisory lock is held",
            )

        scheduler_logger.info(
            "price collection started",
            extra={"event": "price_collection_started"},
        )
        status_counts: Counter[str] = Counter()
        missing_reasons: Counter[str] = Counter()
        auto_adopted_count = estimate_count = missing_count = checkpoint_count = 0
        success_count = review_count = failure_count = ingredient_total = 0
        # finish_run 一定會解鎖並關閉連線，只能呼叫一次；
        # 旗標避免失敗路徑重複呼叫而蓋掉原本的例外
        finish_attempted = False

        try:
            # 續跑前讀取進度也在保護範圍內：失敗時同樣會結束 run 並解鎖
            progress = (
                self.repository.get_run_progress(lease)
                if hasattr(self.repository, "get_run_progress")
                else RunProgress(set(), Counter(), Counter())
            )
            status_counts = progress.status_counts
            missing_reasons = progress.missing_reasons
            auto_adopted_count = progress.auto_adopted_count
            estimate_count = progress.estimate_count
            missing_count = progress.missing_count
            checkpoint_count = len(progress.completed_names)
            success_count = auto_adopted_count
            review_count = estimate_count
            failure_count = missing_count

            ingredients = merge_confirmed_synonyms(self.ingredient_loader(self.workbook_path), self.synonym_groups)
            # 不計價食材（如水）不送去查價，成本為已知 0；在執行摘要中另計
            exempt = [i for i in ingredients if i.food_ids and set(i.food_ids) <= self.exempt_food_ids]
            if exempt:
                ingredients = [i for i in ingredients if i not in exempt]
                status_counts["exempt"] = len(exempt)
            primary_finished = True
            if target_filter is not None:
                # 第二來源只查主要來源查無／不可用的食材；總數即目標數
                targets, primary_finished = target_filter(lease)
                ingredients = [i for i in ingredients if i.canonical_name in targets]
            ingredient_total = len(ingredients)
            self.repository.set_ingredient_total(lease, ingredient_total)
            if food_ids is not None:
                ingredients = [i for i in ingredients if set(i.food_ids or ()) & food_ids]
            selected_ingredients = ingredients[:limit] if limit else ingredients
            remaining = [
                ingredient for ingredient in selected_ingredients
                if ingredient.canonical_name not in progress.completed_names
            ]
            if batch_size:
                remaining = remaining[:batch_size]

            with adapter_factory() as adapter:
                for index, ingredient in enumerate(remaining):
                    representative = None
                    if ingredient.mapping_missing_reason:
                        price_quote = self._workbook_mapping_error_quote(
                            ingredient
                        )
                    else:
                        representative = (
                            self.repository.get_representative(
                                lease, ingredient.canonical_name
                            )
                            if hasattr(self.repository, "get_representative")
                            else None
                        )
                        try:
                            price_quote = adapter.quote(
                                ingredient,
                                preferred_product_id=(
                                    representative.product_id
                                    if representative else None
                                ),
                                representative_product_url=(
                                    representative.product_url
                                    if representative else None
                                ),
                                force_reassessment=(
                                    representative.reassessment_due
                                    or representative.policy_version != POLICY_VERSION
                                    if representative else False
                                ),
                            )
                        except Exception as exc:  # isolate one ingredient failure
                            price_quote = self._unexpected_error_quote(
                                ingredient, type(exc).__name__
                            )
                            if provider != "pxmart":
                                price_quote = price_quote.model_copy(update={
                                    "retailer": getattr(adapter, "retailer", provider),
                                    "source": provider, "source_url": "(fallback source)"})

                    self.repository.save_quote(lease, price_quote)
                    checkpoint_count += 1
                    status_counts[price_quote.status.value] += 1
                    if price_quote.missing_reason:
                        missing_reasons[price_quote.missing_reason] += 1
                    if price_quote.selection_tier is SelectionTier.AUTO_ADOPTED:
                        auto_adopted_count += 1
                        success_count += 1
                        if hasattr(self.repository, "save_representative"):
                            self.repository.save_representative(lease, price_quote)
                    elif price_quote.selection_tier is SelectionTier.ESTIMATE:
                        estimate_count += 1
                        review_count += 1
                    else:
                        missing_count += 1
                        failure_count += 1
                        if representative and hasattr(
                            self.repository, "record_representative_failure"
                        ):
                            self.repository.record_representative_failure(
                                lease, ingredient.canonical_name
                            )

                    if index + 1 < len(remaining) and self.delay_seconds:
                        self.sleep(self.delay_seconds)

            target_total = len(selected_ingredients)
            processed_total = auto_adopted_count + estimate_count + missing_count
            # 主要來源尚未跑完時，之後還會出現新的查無食材：第二來源也維持 partial 以便續跑
            partial = processed_total < target_total or not primary_finished
            final_status = "partial" if partial else (
                "completed" if missing_count == 0 else "completed_with_errors"
            )
            mapping_failure_rate = (
                Decimal(missing_count) / Decimal(processed_total)
                if processed_total else Decimal("0")
            )
            price_update_success_rate = (
                Decimal(auto_adopted_count) / Decimal(processed_total)
                if processed_total else Decimal("0")
            )
            duration_ms = round((time.monotonic() - started_monotonic) * 1000)
            finish_attempted = True
            self.repository.finish_run(
                lease,
                finished_at=datetime.now(timezone.utc),
                status=final_status,
                success_count=success_count,
                review_count=review_count,
                failure_count=failure_count,
                duration_ms=duration_ms,
                status_counts=dict(status_counts),
                missing_reasons=dict(missing_reasons),
                auto_adopted_count=auto_adopted_count,
                estimate_count=estimate_count,
                missing_count=missing_count,
                checkpoint_count=checkpoint_count,
                mapping_failure_rate=mapping_failure_rate,
                price_update_success_rate=price_update_success_rate,
            )
            scheduler_logger.info(
                "price collection completed",
                extra={"event": "price_collection_completed"},
            )
            return CollectionResult(
                run_key=run_key,
                provider=provider,
                status=final_status,
                ingredient_total=ingredient_total,
                success_count=success_count,
                review_count=review_count,
                failure_count=failure_count,
                auto_adopted_count=auto_adopted_count,
                estimate_count=estimate_count,
                missing_count=missing_count,
                checkpoint_count=checkpoint_count,
                mapping_failure_rate=float(mapping_failure_rate),
                price_update_success_rate=float(price_update_success_rate),
                duration_ms=duration_ms,
            )
        except BaseException as exc:
            if finish_attempted:
                # finish_run 已執行過（且已解鎖），不再呼叫第二次
                raise
            finish_attempted = True
            duration_ms = round((time.monotonic() - started_monotonic) * 1000)
            try:
                self.repository.finish_run(
                    lease,
                    finished_at=datetime.now(timezone.utc),
                    status="failed",
                    success_count=success_count,
                    review_count=review_count,
                    failure_count=failure_count,
                    duration_ms=duration_ms,
                    status_counts=dict(status_counts),
                    missing_reasons=dict(missing_reasons),
                    auto_adopted_count=auto_adopted_count,
                    estimate_count=estimate_count,
                    missing_count=missing_count,
                    checkpoint_count=checkpoint_count,
                    fatal_error=type(exc).__name__,
                )
            except Exception:
                # 記錄失敗本身出錯時仍保留原本的例外；finish_run 已確保解鎖
                pass
            scheduler_logger.error(
                "price collection failed",
                extra={"event": "price_collection_failed"},
            )
            raise

    @staticmethod
    def _workbook_mapping_error_quote(
        ingredient: IngredientQuery,
    ) -> PriceQuote:
        return PriceQuote(
            canonical_name=ingredient.canonical_name,
            food_ids=ingredient.food_ids,
            source="recipe workbook Food_ID mapping",
            source_url="data/recipe_dataset_1017.xlsx#recipe_ingredients",
            queried_at=datetime.now(timezone.utc),
            status=QuoteStatus.NOT_FOUND,
            missing_reason=ingredient.mapping_missing_reason,
            selection_method="workbook_mapping",
        )

    @staticmethod
    def _unexpected_error_quote(
        ingredient: IngredientQuery, error_type: str
    ) -> PriceQuote:
        return PriceQuote(
            canonical_name=ingredient.canonical_name,
            food_ids=ingredient.food_ids,
            source_url=SEARCH_URL.format(
                query=url_quote(ingredient.canonical_name, safe="")
            ),
            queried_at=datetime.now(timezone.utc),
            region_condition="Taiwan; no precise delivery region selected",
            store_condition=(
                "Collection failed before a source response could be validated"
            ),
            status=QuoteStatus.SOURCE_ERROR,
            missing_reason=f"unexpected adapter error: {error_type}",
        )
