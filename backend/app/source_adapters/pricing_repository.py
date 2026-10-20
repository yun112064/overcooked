"""PostgreSQL persistence and cross-process locking for T02 collections."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from .price_models import PriceQuote


LOCK_NAME = "overcooked:t02:pxmart-price-collection"
TRY_LOCK_SQL = "SELECT pg_try_advisory_lock(hashtextextended(:lock_name, 0))"
UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended(:lock_name, 0))"


@dataclass
class RunLease:
    run_id: str
    run_key: str
    connection: Connection
    resumed: bool = False
    provider: str = "pxmart"


@dataclass(frozen=True)
class RepresentativeProduct:
    canonical_name: str
    product_id: str
    product_name: str
    product_url: str | None
    confidence: str
    policy_version: str
    consecutive_failures: int
    next_reassessment_at: datetime

    @property
    def reassessment_due(self) -> bool:
        return self.next_reassessment_at <= datetime.now(timezone.utc)


@dataclass
class RunProgress:
    completed_names: set[str]
    status_counts: Counter[str]
    missing_reasons: Counter[str]
    auto_adopted_count: int = 0
    estimate_count: int = 0
    missing_count: int = 0


class PricingRepository:
    """Append-only quote snapshots with one durable row per logical run."""

    def __init__(self, database_engine: Engine | None = None) -> None:
        if database_engine is None:
            from app.db.session import engine as configured_engine

            database_engine = configured_engine
        self.engine = database_engine

    def try_begin_run(
        self,
        *,
        run_key: str,
        provider: str,
        trigger_type: str,
        scheduled_for: datetime,
        started_at: datetime,
    ) -> RunLease | None:
        connection = self.engine.connect()
        locked = False
        try:
            locked = bool(
                connection.execute(
                    text(TRY_LOCK_SQL), {"lock_name": LOCK_NAME}
                ).scalar_one()
            )
            if not locked:
                connection.close()
                return None

            run_id = str(uuid.uuid4())
            inserted = connection.execute(
                text(
                    """
                    INSERT INTO price_collection_runs (
                        id, run_key, provider, trigger_type, scheduled_for,
                        started_at, status, ingredient_total
                    ) VALUES (
                        :id, :run_key, :provider, :trigger_type, :scheduled_for,
                        :started_at, 'running', 0
                    )
                    ON CONFLICT (run_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": run_id,
                    "run_key": run_key,
                    "provider": provider,
                    "trigger_type": trigger_type,
                    "scheduled_for": scheduled_for,
                    "started_at": started_at,
                },
            ).scalar_one_or_none()
            connection.commit()
            resumed = False
            if inserted is None:
                existing = connection.execute(
                    text(
                        "SELECT id, status FROM price_collection_runs "
                        "WHERE run_key = :run_key"
                    ),
                    {"run_key": run_key},
                ).mappings().one()
                if existing["status"] not in {"running", "failed", "partial"}:
                    self._unlock_and_close(connection)
                    return None
                run_id = str(existing["id"])
                resumed = True
                connection.execute(
                    text(
                        "UPDATE price_collection_runs SET status = 'running', "
                        "finished_at = NULL, fatal_error = NULL WHERE id = :id"
                    ),
                    {"id": run_id},
                )
                connection.commit()
            return RunLease(
                run_id=run_id,
                run_key=run_key,
                connection=connection,
                resumed=resumed,
                provider=provider,
            )
        except Exception:
            connection.rollback()
            if locked:
                self._unlock_and_close(connection)
            else:
                connection.close()
            raise

    def set_ingredient_total(self, lease: RunLease, total: int) -> None:
        lease.connection.execute(
            text(
                "UPDATE price_collection_runs "
                "SET ingredient_total = :total WHERE id = :run_id"
            ),
            {"total": total, "run_id": lease.run_id},
        )
        lease.connection.commit()

    def save_quote(self, lease: RunLease, quote: PriceQuote) -> None:
        payload = quote.model_dump(mode="json")
        lease.connection.execute(
            text(
                """
                INSERT INTO price_quote_snapshots (
                    run_id, provider, canonical_name, status, missing_reason,
                    queried_at, valid_from, valid_until, product_id,
                    product_name, price, currency, package_quantity,
                    package_unit, source_url, quote_payload
                ) VALUES (
                    :run_id, :provider, :canonical_name, :status,
                    :missing_reason, :queried_at, :valid_from, :valid_until,
                    :product_id, :product_name, :price, :currency,
                    :package_quantity, :package_unit, :source_url,
                    CAST(:quote_payload AS jsonb)
                )
                ON CONFLICT (run_id, canonical_name) DO NOTHING
                """
            ),
            {
                "run_id": lease.run_id,
                "provider": lease.provider,
                "canonical_name": quote.canonical_name,
                "status": quote.status.value,
                "missing_reason": quote.missing_reason,
                "queried_at": quote.queried_at,
                "valid_from": quote.valid_from,
                "valid_until": quote.valid_until,
                "product_id": quote.product_id,
                "product_name": quote.product_name,
                "price": quote.price,
                "currency": quote.currency,
                "package_quantity": quote.package_quantity,
                "package_unit": quote.package_unit,
                "source_url": quote.source_url,
                "quote_payload": json.dumps(payload, ensure_ascii=False),
            },
        )
        lease.connection.commit()

    def finish_run(
        self,
        lease: RunLease,
        *,
        finished_at: datetime,
        status: str,
        success_count: int,
        review_count: int,
        failure_count: int,
        duration_ms: int,
        status_counts: dict[str, int],
        missing_reasons: dict[str, int],
        auto_adopted_count: int = 0,
        estimate_count: int = 0,
        missing_count: int = 0,
        checkpoint_count: int = 0,
        mapping_failure_rate: Decimal | None = None,
        price_update_success_rate: Decimal | None = None,
        fatal_error: str | None = None,
    ) -> None:
        try:
            lease.connection.rollback()
            lease.connection.execute(
                text(
                    """
                    UPDATE price_collection_runs
                    SET finished_at = :finished_at,
                        status = :status,
                        success_count = :success_count,
                        review_count = :review_count,
                        failure_count = :failure_count,
                        auto_adopted_count = :auto_adopted_count,
                        estimate_count = :estimate_count,
                        missing_count = :missing_count,
                        checkpoint_count = :checkpoint_count,
                        mapping_failure_rate = :mapping_failure_rate,
                        price_update_success_rate = :price_update_success_rate,
                        duration_ms = :duration_ms,
                        status_counts = CAST(:status_counts AS jsonb),
                        missing_reasons = CAST(:missing_reasons AS jsonb),
                        fatal_error = :fatal_error
                    WHERE id = :run_id
                    """
                ),
                {
                    "finished_at": finished_at,
                    "status": status,
                    "success_count": success_count,
                    "review_count": review_count,
                    "failure_count": failure_count,
                    "auto_adopted_count": auto_adopted_count,
                    "estimate_count": estimate_count,
                    "missing_count": missing_count,
                    "checkpoint_count": checkpoint_count,
                    "mapping_failure_rate": mapping_failure_rate,
                    "price_update_success_rate": price_update_success_rate,
                    "duration_ms": duration_ms,
                    "status_counts": json.dumps(status_counts),
                    "missing_reasons": json.dumps(
                        missing_reasons, ensure_ascii=False
                    ),
                    "fatal_error": fatal_error,
                    "run_id": lease.run_id,
                },
            )
            lease.connection.commit()
        finally:
            self._unlock_and_close(lease.connection)

    def get_run_progress(self, lease: RunLease) -> RunProgress:
        rows = lease.connection.execute(
            text(
                "SELECT canonical_name, status, missing_reason, "
                "quote_payload ->> 'selection_tier' AS selection_tier "
                "FROM price_quote_snapshots WHERE run_id = :run_id"
            ),
            {"run_id": lease.run_id},
        ).mappings().all()
        lease.connection.commit()
        progress = RunProgress(set(), Counter(), Counter())
        for row in rows:
            progress.completed_names.add(str(row["canonical_name"]))
            progress.status_counts[str(row["status"])] += 1
            if row["missing_reason"]:
                progress.missing_reasons[str(row["missing_reason"])] += 1
            tier = row["selection_tier"] or "missing"
            if tier == "auto_adopted":
                progress.auto_adopted_count += 1
            elif tier == "estimate":
                progress.estimate_count += 1
            else:
                progress.missing_count += 1
        return progress

    def get_representative(
        self, lease: RunLease, canonical_name: str
    ) -> RepresentativeProduct | None:
        row = lease.connection.execute(
            text(
                "SELECT canonical_name, product_id, product_name, product_url, "
                "confidence, policy_version, consecutive_failures, "
                "next_reassessment_at FROM retailer_product_mappings "
                "WHERE provider = :provider AND canonical_name = :canonical_name "
                "AND active = true"
            ),
            {"canonical_name": canonical_name, "provider": lease.provider},
        ).mappings().one_or_none()
        lease.connection.commit()
        return RepresentativeProduct(**dict(row)) if row else None

    def save_representative(self, lease: RunLease, quote: PriceQuote) -> None:
        if not quote.product_id or not quote.product_name:
            return
        now = datetime.now(timezone.utc)
        lease.connection.execute(
            text(
                """
                INSERT INTO retailer_product_mappings (
                    provider, canonical_name, product_id, product_name,
                    product_url, confidence, selection_reason, policy_version,
                    active, selected_at, last_verified_at, last_success_at,
                    consecutive_failures, next_reassessment_at
                ) VALUES (
                    :provider, :canonical_name, :product_id, :product_name,
                    :product_url, :confidence, CAST(:selection_reason AS jsonb),
                    :policy_version, true, :now, :now, :now, 0, :reassess_at
                )
                ON CONFLICT (provider, canonical_name) DO UPDATE SET
                    product_id = EXCLUDED.product_id,
                    product_name = EXCLUDED.product_name,
                    product_url = EXCLUDED.product_url,
                    confidence = EXCLUDED.confidence,
                    selection_reason = EXCLUDED.selection_reason,
                    policy_version = EXCLUDED.policy_version,
                    active = true,
                    last_verified_at = EXCLUDED.last_verified_at,
                    last_success_at = EXCLUDED.last_success_at,
                    consecutive_failures = 0,
                    next_reassessment_at = EXCLUDED.next_reassessment_at,
                    selected_at = CASE
                        WHEN retailer_product_mappings.product_id <> EXCLUDED.product_id
                        THEN EXCLUDED.selected_at
                        ELSE retailer_product_mappings.selected_at
                    END
                """
            ),
            {
                "provider": lease.provider,
                "canonical_name": quote.canonical_name,
                "product_id": quote.product_id,
                "product_name": quote.product_name,
                "product_url": quote.product_url,
                "confidence": quote.confidence.value,
                "selection_reason": json.dumps(
                    quote.selection_reasons, ensure_ascii=False
                ),
                "policy_version": quote.selection_policy_version,
                "now": now,
                "reassess_at": now + timedelta(days=30),
            },
        )
        lease.connection.commit()

    def record_representative_failure(
        self, lease: RunLease, canonical_name: str
    ) -> None:
        lease.connection.execute(
            text(
                "UPDATE retailer_product_mappings SET "
                "consecutive_failures = consecutive_failures + 1, "
                "last_verified_at = :now, "
                "active = CASE WHEN consecutive_failures + 1 >= 3 "
                "THEN false ELSE active END "
                "WHERE provider = :provider AND canonical_name = :canonical_name"
            ),
            {"canonical_name": canonical_name, "now": datetime.now(timezone.utc),
             "provider": lease.provider},
        )
        lease.connection.commit()

    def fallback_targets(self, lease: RunLease, *, primary_run_key: str) -> tuple[set[str], bool]:
        """主要來源（全聯）那次 run 查無／不可用的食材，以及該 run 是否已跑完。

        只取 selection_tier=missing（not_found、unusable_package、out_of_stock、逾時等）；
        review_required（估價待審）與工作簿 Food_ID 對應失敗不送第二來源。
        """
        row = lease.connection.execute(
            text("SELECT id, status FROM price_collection_runs WHERE run_key = :run_key"),
            {"run_key": primary_run_key},
        ).mappings().one_or_none()
        if row is None:
            lease.connection.commit()
            return set(), False
        names = lease.connection.execute(
            text(
                "SELECT canonical_name FROM price_quote_snapshots WHERE run_id = :run_id "
                "AND status <> 'review_required' "
                "AND COALESCE(quote_payload ->> 'selection_tier', 'missing') = 'missing' "
                "AND COALESCE(quote_payload ->> 'selection_method', '') <> 'workbook_mapping'"
            ),
            {"run_id": row["id"]},
        ).scalars().all()
        lease.connection.commit()
        finished = row["status"] in {"completed", "completed_with_errors"}
        return set(names), finished

    def list_recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        query, params = run_rows_query(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(text(query), params).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _unlock_and_close(connection: Connection) -> None:
        """釋放 session 級 advisory lock 後關閉連線。

        前一個指令失敗時交易會處於 aborted 狀態，直接 unlock 也會失敗；
        因此先 rollback。若仍無法 unlock，就丟棄這條連線（invalidate），
        不讓它帶著鎖回到連線池，PostgreSQL 會在連線中斷時自動釋放鎖。
        這裡不往外拋例外，以免蓋掉呼叫端原本的錯誤。
        """
        try:
            connection.rollback()
            connection.execute(text(UNLOCK_SQL), {"lock_name": LOCK_NAME})
            connection.commit()
        except Exception:
            connection.invalidate()
        finally:
            connection.close()


def run_rows_query(limit: int = 20) -> tuple[str, dict[str, Any]]:
    """Return a safe read-only query used by the documentation/operations CLI."""

    return (
        """
        SELECT run_key, provider, trigger_type, scheduled_for, started_at,
               finished_at, status, ingredient_total, success_count,
               review_count, failure_count, auto_adopted_count,
               estimate_count, missing_count, checkpoint_count,
               mapping_failure_rate, price_update_success_rate,
               duration_ms, status_counts, missing_reasons, fatal_error
        FROM price_collection_runs
        ORDER BY started_at DESC
        LIMIT :limit
        """,
        {"limit": max(1, min(limit, 100))},
    )
