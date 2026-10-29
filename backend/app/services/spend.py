"""T10 付費呼叫額度：呼叫前原子預留最大可能費用，完成後依 usage 結算。

月額度涵蓋全部外部服務（committed 為主機、備份等固定占用），不只模型費用。
預留與結算各用短交易完成，外部呼叫本身不在交易內。
"""
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.models import BudgetMonth, UsageReservation

TAIPEI = ZoneInfo("Asia/Taipei")
MONEY = Decimal("0.0001")


class BudgetExhausted(Exception):
    """可用額度不足：停止新增付費呼叫，其他功能照常。"""


class CallNotBilled(Exception):
    """供應商轉接器確定此次呼叫沒有計費（例如連線前就失敗），可安全重試。"""


class UncertainBilling(Exception):
    """呼叫可能已計費但結果不明；保留預留並等待人工核對，不自動重送。"""


@lru_cache(maxsize=1)
def _pricing(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pricing() -> dict:
    return _pricing(str(settings.model_pricing_path))


def month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(TAIPEI).strftime("%Y-%m")


def cost_twd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    table = pricing()
    entry = table["models"].get(model)
    if entry is None:
        # 未列價的模型不能以 0 計價
        raise ValueError(f"Model {model!r} has no pricing entry; refusing paid call.")
    usd = (Decimal(entry["input"]) * input_tokens + Decimal(entry["output"]) * output_tokens) / Decimal(1_000_000)
    return (usd * settings.usd_twd_rate).quantize(MONEY, rounding=ROUND_CEILING)


def ensure_month(db: Session, month: str) -> None:
    db.execute(text(
        "INSERT INTO budget_months (month, limit_twd, committed_twd, reserved_twd, spent_twd, updated_at) "
        "VALUES (:m, :limit, :committed, 0, 0, now()) ON CONFLICT (month) DO NOTHING"
    ), {"m": month, "limit": settings.monthly_budget_twd, "committed": settings.committed_services_twd})


def remaining_twd(db: Session, month: str | None = None) -> Decimal:
    month = month or month_key()
    ensure_month(db, month)
    row = db.get(BudgetMonth, month)
    return row.limit_twd - row.committed_twd - row.reserved_twd - row.spent_twd


@dataclass(frozen=True)
class Reservation:
    id: object
    call_key: str
    model: str
    reserved_twd: Decimal


def reserve(factory: sessionmaker, *, call_key: str, purpose: str, provider: str, model: str,
            max_input_tokens: int, max_output_tokens: int, job_id=None, household_id=None) -> Reservation:
    amount = cost_twd(model, max_input_tokens, max_output_tokens)
    month = month_key()
    with factory() as db, db.begin():
        existing = db.scalar(select(UsageReservation).where(UsageReservation.call_key == call_key))
        if existing is not None:
            # 同一呼叫鍵不得預留兩次（避免重送造成重複計費）
            raise UncertainBilling(f"Call {call_key} already has a reservation ({existing.status}).")
        ensure_month(db, month)
        # 條件式 UPDATE 讓並行預留由資料庫原子判斷，不會超過上限
        updated = db.execute(text(
            "UPDATE budget_months SET reserved_twd = reserved_twd + :amt, updated_at = now() "
            "WHERE month = :m AND committed_twd + reserved_twd + spent_twd + :amt <= limit_twd "
            "RETURNING month"
        ), {"amt": amount, "m": month}).first()
        if updated is None:
            raise BudgetExhausted("Monthly external-service budget would be exceeded.")
        row = UsageReservation(
            call_key=call_key, job_id=job_id, household_id=household_id, month=month, purpose=purpose,
            provider=provider, model=model, status="reserved", reserved_twd=amount, currency="USD",
            fx_rate_twd=settings.usd_twd_rate, pricing_version=pricing()["pricing_version"],
        )
        db.add(row)
        db.flush()
        return Reservation(row.id, call_key, model, amount)


def _finish(factory: sessionmaker, reservation_id, *, status: str, actual: Decimal | None,
            usage: dict | None, note: str | None = None) -> None:
    with factory() as db, db.begin():
        row = db.scalar(select(UsageReservation).where(UsageReservation.id == reservation_id).with_for_update())
        if row is None or row.status not in ("reserved", "uncertain"):
            raise ValueError("Reservation is not open.")
        ledger = db.scalar(select(BudgetMonth).where(BudgetMonth.month == row.month).with_for_update())
        if status == "uncertain":
            # 仍計入預留，直到人工核對
            row.status, row.note = "uncertain", note
            return
        ledger.reserved_twd -= row.reserved_twd
        if actual is not None:
            ledger.spent_twd += actual
        ledger.updated_at = datetime.now(timezone.utc)
        row.status, row.actual_twd, row.usage, row.note = status, actual, usage, note
        row.settled_at = datetime.now(timezone.utc)


def settle(factory: sessionmaker, reservation: Reservation, *, input_tokens: int, output_tokens: int) -> Decimal:
    actual = cost_twd(reservation.model, input_tokens, output_tokens)
    _finish(factory, reservation.id, status="settled", actual=actual,
            usage={"input_tokens": input_tokens, "output_tokens": output_tokens})
    return actual


def release(factory: sessionmaker, reservation: Reservation, note: str) -> None:
    _finish(factory, reservation.id, status="released", actual=None, usage=None, note=note)


def mark_uncertain(factory: sessionmaker, reservation_id, note: str) -> None:
    _finish(factory, reservation_id, status="uncertain", actual=None, usage=None, note=note)


def reconcile(factory: sessionmaker, reservation_id, *, actual_twd: Decimal, note: str) -> None:
    """管理者依供應商帳單核對 uncertain 呼叫（實際未計費則填 0）。"""
    _finish(factory, reservation_id, status="settled", actual=Decimal(actual_twd).quantize(MONEY),
            usage=None, note=note)


def paid_call(factory: sessionmaker, *, call_key: str, purpose: str, provider: str, model: str,
              max_input_tokens: int, max_output_tokens: int, call: Callable[[], tuple[object, dict]],
              job_id=None, household_id=None):
    """預留 → 呼叫 → 結算。call 回傳 (結果, {"input_tokens", "output_tokens"})。

    CallNotBilled：確定未計費，釋放預留後往上拋，可重試。
    其他例外：可能已計費，標為 uncertain 並拋出 UncertainBilling，不自動重送。
    """
    reservation = reserve(factory, call_key=call_key, purpose=purpose, provider=provider, model=model,
                          max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens,
                          job_id=job_id, household_id=household_id)
    try:
        result, usage = call()
    except CallNotBilled as exc:
        release(factory, reservation, note=f"not billed: {type(exc).__name__}")
        raise
    except Exception as exc:
        mark_uncertain(factory, reservation.id, note=f"outcome unknown: {type(exc).__name__}")
        raise UncertainBilling(str(exc)) from exc
    settle(factory, reservation, input_tokens=int(usage["input_tokens"]), output_tokens=int(usage["output_tokens"]))
    return result
