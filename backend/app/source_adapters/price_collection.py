"""One resilient PX Mart price collection run."""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass
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
    ) -> None:
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
    ) -> CollectionResult:
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        if run_key is None:
            run_key = f"pxmart-manual-{uuid.uuid4()}"

        lease = self.repository.try_begin_run(
            run_key=run_key,
            provider="pxmart",
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
                status="skipped",
                skipped_reason="run_key already exists or advisory lock is held",
            )

        scheduler_logger.info(
            "price collection started",
            extra={"event": "price_collection_started"},
        )
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
        ingredient_total = 0

        try:
            ingredients = self.ingredient_loader(self.workbook_path)
            ingredient_total = len(ingredients)
            self.repository.set_ingredient_total(lease, ingredient_total)
            selected_ingredients = ingredients[:limit] if limit else ingredients
            remaining = [
                ingredient for ingredient in selected_ingredients
                if ingredient.canonical_name not in progress.completed_names
            ]
            if batch_size:
                remaining = remaining[:batch_size]

            with self.adapter_factory() as adapter:
                for index, ingredient in enumerate(remaining):
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
                                if representative else False
                            ),
                        )
                    except Exception as exc:  # isolate one ingredient failure
                        price_quote = self._unexpected_error_quote(
                            ingredient, type(exc).__name__
                        )

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
            partial = processed_total < target_total
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
        except Exception as exc:
            duration_ms = round((time.monotonic() - started_monotonic) * 1000)
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
            scheduler_logger.error(
                "price collection failed",
                extra={"event": "price_collection_failed"},
            )
            raise

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
