"""冪等送出的共用工具：(household, Idempotency-Key) 唯一＋命令雜湊（request hash）。

同鍵同命令 → 回傳既有紀錄；同鍵不同命令 → 409 IDEMPOTENCY_CONFLICT。
T10 生成送出、T15 結果決定、T16 採用共用此規則。
"""
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import idempotency_conflict


def request_hash(*parts) -> str:
    """命令雜湊：各部分以「:」串接後取 SHA-256（None 以「-」表示）。"""
    return hashlib.sha256(":".join("-" if p is None else str(p) for p in parts).encode()).hexdigest()


def find_by_key(db: Session, model, household_id, key: str, digest: str, *, message: str):
    """依 (household_id, idempotency_key) 找既有紀錄；雜湊不同即 409。model 需有這三個欄位。"""
    row = db.scalar(select(model).where(model.household_id == household_id, model.idempotency_key == key))
    if row is not None and row.request_hash != digest:
        raise idempotency_conflict(message)
    return row
