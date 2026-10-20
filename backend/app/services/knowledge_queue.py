"""G07 待補知識食材佇列（雲端）：生成結果的最終食材缺審核知識時記錄 FOOD 編號，供團隊電腦爬蟲優先處理。

- record_missing 只在知識快照可用（available／not_found）時記錄 by_ingredient == not_found 的食材；
  服務不可用時不知道是否缺知識，不記錄。任何錯誤都吞掉並回傳空清單：絕不阻擋或延誤生成。
- 只存 FOOD 編號、次數與時間；不存家庭、使用者或問卷資料。
- 團隊電腦經雲端內部 API（api/internal_knowledge.py）claim → mark_crawled，不直接連雲端資料庫；
  認領超過 CLAIM_TTL 未回報的請求可再被認領。
- 已處理（crawled）的食材在 RECRAWL_AFTER 內再被要求只增加次數，不重新排入，避免每次生成都重抓。
- 佇列只是「去抓」的提示：抓到的內容一律 pending，審核通過並重建索引前不會顯示。
"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from app.core.logging_config import process_logger
from app.db.models import KnowledgeCrawlRequest as Req

RECRAWL_AFTER = timedelta(days=7)
CLAIM_TTL = timedelta(hours=2)
FOOD_ID = re.compile(r"^FOOD\d{6}$")


def missing_food_ids(snapshot: dict) -> list[str]:
    if not isinstance(snapshot, dict) or snapshot.get("status") not in ("available", "not_found"):
        return []
    by = snapshot.get("by_ingredient") or {}
    return sorted(i for i, s in by.items() if s == "not_found" and isinstance(i, str) and FOOD_ID.match(i))


def _upsert(factory: sessionmaker, food_ids: list[str], now: datetime) -> None:
    table = Req.__table__
    with factory() as db, db.begin():
        for food_id in food_ids:
            stmt = insert(table).values(food_id=food_id, status="pending", request_count=1,
                                        first_requested_at=now, last_requested_at=now)
            reopen = (table.c.status == "crawled") & (table.c.last_crawled_at < now - RECRAWL_AFTER)
            db.execute(stmt.on_conflict_do_update(index_elements=[table.c.food_id], set_={
                "request_count": table.c.request_count + 1,
                "last_requested_at": now,
                "status": case((reopen, "pending"), else_=table.c.status),
            }))


def record_missing(factory: sessionmaker, snapshot: dict, *, now: datetime | None = None) -> list[str]:
    """回傳已記錄的 FOOD 編號；失敗時記一行不含內容的警告並回傳空清單。"""
    try:
        food_ids = missing_food_ids(snapshot)
        if food_ids:
            _upsert(factory, food_ids, now or datetime.now(timezone.utc))
        return food_ids
    except Exception:  # noqa: BLE001 — 佇列是盡力而為，絕不影響生成
        process_logger.warning("knowledge crawl queue unavailable", extra={"event": "knowledge_queue_unavailable"})
        return []


def _public(row: Req) -> dict:
    return {"food_id": row.food_id, "request_count": row.request_count,
            "first_requested_at": row.first_requested_at, "last_requested_at": row.last_requested_at}


def _order():
    return (Req.request_count.desc(), Req.first_requested_at, Req.food_id)


def pending(factory: sessionmaker, *, limit: int = 50) -> list[dict]:
    with factory() as db:
        rows = db.scalars(select(Req).where(Req.status == "pending").order_by(*_order()).limit(limit)).all()
        return [_public(r) for r in rows]


def pending_count(factory: sessionmaker) -> int:
    with factory() as db:
        return db.scalar(select(func.count()).select_from(Req).where(Req.status == "pending")) or 0


def claim(factory: sessionmaker, *, limit: int = 20, now: datetime | None = None) -> list[dict]:
    """認領待補食材（需求次數多者優先）；逾時未回報的認領視同待處理。多個爬蟲不會拿到同一筆。"""
    now = now or datetime.now(timezone.utc)
    with factory() as db, db.begin():
        rows = db.scalars(select(Req).where(or_(
            Req.status == "pending", (Req.status == "claimed") & (Req.claimed_at < now - CLAIM_TTL)))
            .order_by(*_order()).limit(limit).with_for_update(skip_locked=True)).all()
        for row in rows:
            row.status, row.claimed_at = "claimed", now
        return [_public(r) for r in rows]


def mark_crawled(factory: sessionmaker, outcomes: dict[str, dict], *, now: datetime | None = None) -> int:
    """outcomes：FOOD 編號 → 抓取後的知識數（例如 {"pending": 1, "approved": 0}）。回傳更新筆數。"""
    now = now or datetime.now(timezone.utc)
    updated = 0
    with factory() as db, db.begin():
        for food_id, outcome in outcomes.items():
            row = db.get(Req, food_id, with_for_update=True)
            if row is None:
                continue
            row.status, row.last_crawled_at, row.last_outcome, row.claimed_at = "crawled", now, dict(outcome), None
            updated += 1
    return updated
