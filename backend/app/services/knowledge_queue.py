"""G07 待補知識食材佇列（雲端）：生成結果的最終食材缺審核知識時記錄 FOOD 編號，供團隊電腦爬蟲優先處理。

- record_missing 只在知識快照可用（available／not_found）時記錄 by_ingredient == not_found 的食材；
  服務不可用時不知道是否缺知識，不記錄。任何錯誤都吞掉並回傳空清單：絕不阻擋或延誤生成。
- 只存 FOOD 編號、次數與時間；不存家庭、使用者或問卷資料。
- 已處理（crawled）的食材在 RECRAWL_AFTER 內再被要求只增加次數，不重新排入，避免每次生成都重抓。
- 佇列只是「去抓」的提示：抓到的內容一律 pending，審核通過並重建索引前不會顯示。
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

RECRAWL_AFTER = timedelta(days=7)
FOOD_ID = re.compile(r"^FOOD\d{6}$")
log = logging.getLogger(__name__)


def missing_food_ids(snapshot: dict) -> list[str]:
    if not isinstance(snapshot, dict) or snapshot.get("status") not in ("available", "not_found"):
        return []
    by = snapshot.get("by_ingredient") or {}
    return sorted(i for i, s in by.items() if s == "not_found" and isinstance(i, str) and FOOD_ID.match(i))


def _upsert(factory: sessionmaker, food_ids: list[str], now: datetime) -> None:
    with factory() as db, db.begin():
        for food_id in food_ids:
            db.execute(text("""
                INSERT INTO knowledge_crawl_requests (food_id, status, request_count, first_requested_at,
                                                      last_requested_at)
                VALUES (:id, 'pending', 1, :now, :now)
                ON CONFLICT (food_id) DO UPDATE SET
                    request_count = knowledge_crawl_requests.request_count + 1,
                    last_requested_at = :now,
                    status = CASE WHEN knowledge_crawl_requests.status = 'crawled'
                                   AND knowledge_crawl_requests.last_crawled_at < :reopen
                                  THEN 'pending' ELSE knowledge_crawl_requests.status END
            """), {"id": food_id, "now": now, "reopen": now - RECRAWL_AFTER})


def record_missing(factory: sessionmaker, snapshot: dict, *, now: datetime | None = None) -> list[str]:
    """回傳已記錄的 FOOD 編號；失敗時記一行不含內容的警告並回傳空清單。"""
    try:
        food_ids = missing_food_ids(snapshot)
        if food_ids:
            _upsert(factory, food_ids, now or datetime.now(timezone.utc))
        return food_ids
    except Exception:  # noqa: BLE001 — 佇列是盡力而為，絕不影響生成
        log.warning("knowledge crawl queue unavailable; skipped")
        return []


def pending(factory: sessionmaker, *, limit: int = 50) -> list[dict]:
    with factory() as db:
        rows = db.execute(text(
            "SELECT food_id, request_count, first_requested_at, last_requested_at FROM knowledge_crawl_requests "
            "WHERE status = 'pending' ORDER BY request_count DESC, first_requested_at, food_id LIMIT :n"),
            {"n": limit}).mappings().all()
    return [dict(r) for r in rows]


def mark_crawled(factory: sessionmaker, outcomes: dict[str, dict], *, now: datetime | None = None) -> None:
    """outcomes：FOOD 編號 → 抓取後的知識數（例如 {"pending": 1, "approved": 0}）。"""
    now = now or datetime.now(timezone.utc)
    with factory() as db, db.begin():
        for food_id, outcome in outcomes.items():
            db.execute(text("UPDATE knowledge_crawl_requests SET status = 'crawled', last_crawled_at = :now, "
                            "last_outcome = CAST(:outcome AS jsonb) WHERE food_id = :id"),
                       {"id": food_id, "now": now, "outcome": json.dumps(outcome)})
