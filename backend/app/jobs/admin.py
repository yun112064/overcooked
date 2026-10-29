"""T10 費用追查與核對：python -m app.jobs.admin {budget|reconcile}

budget：顯示本月額度帳與尚未結清（reserved／uncertain）的付費呼叫。
reconcile：依供應商實際帳單結清 uncertain 呼叫（未計費請填 0）。
"""
import argparse
import json
from decimal import Decimal

from sqlalchemy import select

from app.db.models import BudgetMonth, UsageReservation
from app.services import spend


def budget_report(factory, month: str | None = None) -> dict:
    month = month or spend.month_key()
    with factory() as db, db.begin():
        spend.ensure_month(db, month)
        ledger = db.get(BudgetMonth, month)
        open_rows = db.scalars(select(UsageReservation).where(
            UsageReservation.month == month, UsageReservation.status.in_(("reserved", "uncertain")))
            .order_by(UsageReservation.created_at)).all()
        remaining = ledger.limit_twd - ledger.committed_twd - ledger.reserved_twd - ledger.spent_twd
        return {
            "month": month,
            "limit_twd": str(ledger.limit_twd),
            "committed_twd": str(ledger.committed_twd),
            "reserved_twd": str(ledger.reserved_twd),
            "spent_twd": str(ledger.spent_twd),
            "remaining_twd": str(remaining),
            "open_calls": [
                {"id": str(r.id), "status": r.status, "job_id": str(r.job_id) if r.job_id else None,
                 "model": r.model, "reserved_twd": str(r.reserved_twd), "note": r.note,
                 "created_at": r.created_at.isoformat()}
                for r in open_rows
            ],
        }


def main() -> None:
    from app.db.session import SessionLocal

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("budget")
    b.add_argument("--month")
    r = sub.add_parser("reconcile")
    r.add_argument("reservation_id")
    r.add_argument("actual_twd", type=Decimal)
    r.add_argument("note")
    args = parser.parse_args()
    if args.command == "budget":
        print(json.dumps(budget_report(SessionLocal, args.month), ensure_ascii=False, indent=2))
    else:
        spend.reconcile(SessionLocal, args.reservation_id, actual_twd=args.actual_twd, note=args.note)
        print("reconciled")


if __name__ == "__main__":
    main()
