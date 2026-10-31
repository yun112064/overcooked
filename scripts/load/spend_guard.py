"""T18 額度防繞過驗證（AC25）：多人並行＋各種重試下，T10 預留帳本永不超過月上限。

直接使用 app.services.spend（不另寫一套），付費呼叫一律是假的、不連任何供應商。
帳本寫在合成月份（預設 2099-12），結束後清除，不影響真實月份。

用法（repo 根目錄，需能 import backend 的 app）：
  DATABASE_URL=... PYTHONPATH=backend python scripts/load/spend_guard.py --database-url <測試 DB URL>
"""
import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import text

MODEL = "gpt-5.4-mini-2026-03-17"
FORBIDDEN_DATABASES = {"meal_planner_dev", "meal_planner", "postgres"}


def assert_safe_database(url: str) -> None:
    name = urlsplit(url.replace("+psycopg", "")).path.lstrip("/")
    if name in FORBIDDEN_DATABASES or name.endswith(("_dev", "_prod")):
        raise ValueError(f"Refusing to run the spend guard check against database {name!r}.")


@contextmanager
def _synthetic_month(spend, month: str):
    original = spend.month_key
    spend.month_key = lambda now=None: month
    try:
        yield
    finally:
        spend.month_key = original


def _cleanup(factory, month: str) -> None:
    with factory() as db, db.begin():
        db.execute(text("DELETE FROM usage_reservations WHERE month = :m"), {"m": month})
        db.execute(text("DELETE FROM budget_months WHERE month = :m"), {"m": month})


def _ledger_total(factory, month: str) -> tuple[Decimal, Decimal]:
    with factory() as db:
        row = db.execute(text("SELECT committed_twd + reserved_twd + spent_twd, limit_twd FROM budget_months "
                              "WHERE month = :m"), {"m": month}).one()
        return Decimal(row[0]), Decimal(row[1])


def run_spend_guard_check(factory, *, users: int = 5, calls_per_user: int = 12, month: str = "2099-12",
                          limit_twd: Decimal = Decimal("2000"), committed_twd: Decimal = Decimal("1998"),
                          model: str = MODEL, max_input_tokens: int = 3000, max_output_tokens: int = 1000,
                          cleanup: bool = True) -> dict:
    """每位使用者輪流執行四種情境：正常結算、未計費失敗後換鍵重試、同鍵重送、結果不明後同鍵重送。"""
    from app.services import spend

    lock = threading.Lock()
    counts = {"settled": 0, "released": 0, "uncertain": 0, "budget_exhausted": 0,
              "duplicate_attempts": 0, "duplicate_refused": 0}
    max_seen = [Decimal(0)]

    def bump(key):
        with lock:
            counts[key] += 1

    def observe():
        total, _ = _ledger_total(factory, month)
        with lock:
            max_seen[0] = max(max_seen[0], total)

    def paid(call_key, behaviour):
        def call():
            observe()  # 呼叫進行中（已預留）時的帳本占用
            if behaviour == "not_billed":
                raise spend.CallNotBilled("connection refused before send")
            if behaviour == "uncertain":
                raise TimeoutError("read timeout after request was sent")
            return "ok", {"input_tokens": max_input_tokens // 2, "output_tokens": max_output_tokens // 2}
        return spend.paid_call(factory, call_key=call_key, purpose="t18-guard", provider="fake", model=model,
                               max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens, call=call)

    def duplicate(call_key):
        bump("duplicate_attempts")
        try:
            paid(call_key, "ok")
        except spend.UncertainBilling:
            bump("duplicate_refused")
        except spend.BudgetExhausted:
            bump("duplicate_refused")
            bump("budget_exhausted")
        else:
            bump("settled")

    def worker(user: int):
        for i in range(calls_per_user):
            key = f"t18-{month}-u{user}-c{i}"
            scenario = i % 4
            try:
                if scenario == 1:
                    try:
                        paid(key, "not_billed")
                    except spend.CallNotBilled:
                        bump("released")
                    paid(key + "-retry", "ok")  # 確定未計費：換新呼叫鍵重試
                    bump("settled")
                elif scenario == 3:
                    try:
                        paid(key, "uncertain")
                    except spend.UncertainBilling:
                        bump("uncertain")
                    duplicate(key)  # 結果不明：同鍵重送必須被拒
                else:
                    paid(key, "ok")
                    bump("settled")
                    if scenario == 2:
                        duplicate(key)  # 已結算後同鍵重送必須被拒
            except spend.BudgetExhausted:
                bump("budget_exhausted")

    _cleanup(factory, month)
    with factory() as db, db.begin():
        db.execute(text("INSERT INTO budget_months (month, limit_twd, committed_twd, reserved_twd, spent_twd, "
                        "updated_at) VALUES (:m, :l, :c, 0, 0, now())"),
                   {"m": month, "l": limit_twd, "c": committed_twd})
    try:
        with _synthetic_month(spend, month):
            with ThreadPoolExecutor(max_workers=users) as pool:
                list(pool.map(worker, range(users)))
        final_total, limit = _ledger_total(factory, month)
        with factory() as db:
            ledger = db.execute(text("SELECT reserved_twd, spent_twd FROM budget_months WHERE month = :m"),
                                {"m": month}).one()
            sums = db.execute(text(
                "SELECT coalesce(sum(reserved_twd) FILTER (WHERE status IN ('reserved','uncertain')), 0), "
                "coalesce(sum(actual_twd) FILTER (WHERE status = 'settled'), 0), count(*), count(DISTINCT call_key) "
                "FROM usage_reservations WHERE month = :m"), {"m": month}).one()
    finally:
        if cleanup:
            _cleanup(factory, month)

    checks = [
        {"name": "ledger_never_over_limit", "passed": max(max_seen[0], final_total) <= limit,
         "detail": {"max_observed_twd": str(max(max_seen[0], final_total)), "limit_twd": str(limit)}},
        {"name": "duplicate_call_key_refused",
         "passed": counts["duplicate_attempts"] > 0 and counts["duplicate_refused"] == counts["duplicate_attempts"],
         "detail": {"attempts": counts["duplicate_attempts"], "refused": counts["duplicate_refused"]}},
        {"name": "budget_exhausted_reached", "passed": counts["budget_exhausted"] > 0,
         "detail": {"count": counts["budget_exhausted"]}},
        {"name": "ledger_matches_reservations",
         "passed": Decimal(ledger[0]) == Decimal(sums[0]) and Decimal(ledger[1]) == Decimal(sums[1]),
         "detail": {"ledger_reserved": str(ledger[0]), "rows_reserved": str(sums[0]),
                    "ledger_spent": str(ledger[1]), "rows_spent": str(sums[1])}},
        {"name": "one_reservation_per_call_key", "passed": sums[2] == sums[3],
         "detail": {"rows": sums[2], "distinct_call_keys": sums[3]}},
    ]
    return {"month": month, "users": users, "calls_per_user": calls_per_user, "counts": counts,
            "checks": checks, "passed": all(c["passed"] for c in checks)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="T18 額度防繞過驗證（只用假呼叫）")
    p.add_argument("--database-url", required=True, help="測試／staging 資料庫；拒絕 *_dev 與 *_prod")
    p.add_argument("--users", type=int, default=5)
    p.add_argument("--calls-per-user", type=int, default=12)
    p.add_argument("--month", default="2099-12")
    args = p.parse_args(argv)
    try:
        assert_safe_database(args.database_url)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=create_engine(args.database_url), autoflush=False, expire_on_commit=False)
    report = run_spend_guard_check(factory, users=args.users, calls_per_user=args.calls_per_user, month=args.month)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
