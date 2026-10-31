"""T18 五人並行負載腳本（AC21／G06）。

每位使用者各自登入一個測試家庭，同時（以 barrier 對齊每一輪起點）走完：
登入／家庭 → 問卷 → 暖機辨識＋確認 → 前一日歷史讀取 → 資料庫菜單生成（結構化查詢，含排隊）
→ 自動 AI 回退生成 → 結果（含知識 RAG 狀態與採用資格）→ 採用（後端重驗前一日）→ 依日期歷史。

記錄每一步延遲；生成時間從送出到工作完成（含排隊）。輸出 JSON＋Markdown 報告（load_report）。
門檻只能收緊；dry-run 只對本機假伺服器，結論永遠是 not_verified。

用法（repo 根目錄）：
  python scripts/load/five_user_load.py --dry-run
  python scripts/load/five_user_load.py --base-url https://<部署網址>/api \
      --scenario my-scenario.json --environment my-env.json --iterations 6
"""
import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import load_report as lr  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_SCENARIO = HERE / "scenario.example.json"
DEFAULT_OUT = HERE / "results"
FLOW_STEPS = (
    "login", "household", "member_create", "me", "questionnaire_create", "questionnaire_read",
    "recognition", "recognition_confirm", "previous_day_read", "generation_submit", "job_poll",
    "generation_database", "fallback_questionnaire_create", "fallback_submit", "generation_fallback",
    "result_view", "adoption", "history_read", "history_detail",
)
STEP_CATEGORY = {"recognition": "recognition_warm", "recognition_warmup": "warmup",
                 "generation_database": "generation", "generation_fallback": "generation"}
# 不是真的 JPEG；只給假伺服器使用
DRY_RUN_IMAGE = b"\xff\xd8\xff\xe0T18-dry-run\xff\xd9"


@dataclass
class LoadResult:
    samples: list = field(default_factory=list)
    issues: list = field(default_factory=list)


class StepFailed(Exception):
    pass


def load_scenario(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["_path"] = str(path)
    data["_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return data


def asgi_transport(app) -> httpx.AsyncBaseTransport:
    return httpx.ASGITransport(app=app)


class UserSession:
    def __init__(self, index: int, client: httpx.AsyncClient, result: LoadResult, run_id: str):
        self.index, self.client, self.result, self.run_id = index, client, result, run_id
        self.iteration = 0
        self.member_id = None

    async def call(self, step: str, method: str, url: str, *, expect=(200, 201, 202, 204), **kw) -> httpx.Response | None:
        started = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        try:
            resp = await self.client.request(method, url, **kw)
            status, ok, detail = resp.status_code, resp.status_code in expect, {}
            if not ok:
                detail = {"error": _error_code(resp)}
        except httpx.HTTPError as exc:
            resp, status, ok, detail = None, None, False, {"error": type(exc).__name__}
        self.record(step, time.perf_counter() - t0, ok, status, started, detail)
        if not ok:
            raise StepFailed(step)
        return resp

    def record(self, step, latency, ok, status=None, started=None, detail=None):
        sample = lr.Sample(step=step, category=STEP_CATEGORY.get(step, "api"), user=self.index,
                           iteration=self.iteration, latency_s=latency, ok=ok, status_code=status,
                           started_at=started, detail=detail or {})
        self.result.samples.append(sample)
        return sample

    def issue(self, code: str, **details):
        self.result.issues.append({"code": code, "user": self.index, "iteration": self.iteration, **details})


def _error_code(resp: httpx.Response):
    try:
        return resp.json()["error"]["code"]
    except Exception:
        return None


async def setup_user(s: UserSession, scenario: dict, account: dict) -> None:
    resp = await s.call("login", "POST", "/auth/register" if account.get("register") else "/auth/login",
                        json={"email": account["email"], "password": account["password"]})
    if resp.status_code == 201 or not resp.json().get("current_household"):
        listing = (await s.call("household", "GET", "/household")).json()
        if not listing.get("current_household_id"):
            await s.call("household", "POST", "/household", json={"name": scenario["household_name"]})
    member = (await s.call("member_create", "POST", "/members", json=scenario["member"])).json()
    s.member_id = member["id"]


async def generate(s: UserSession, step: str, request_id: str, version: int, *, submit_step: str,
                   poll_interval_s: float, timeout_s: float) -> dict | None:
    """送出到工作結束（含排隊）為一筆生成樣本。"""
    t0 = time.perf_counter()
    started = datetime.now(timezone.utc).isoformat()
    try:
        job = (await s.call(submit_step, "POST", f"/meal-requests/{request_id}/submit", json={"version": version},
                            headers={"Idempotency-Key": f"t18-{s.run_id}-{uuid.uuid4().hex[:12]}"})).json()["job"]
        polls = 0
        while job["status"] not in ("succeeded", "failed"):
            if time.perf_counter() - t0 > timeout_s:
                s.record(step, time.perf_counter() - t0, False, None, started, {"error": "TIMEOUT", "polls": polls})
                return None
            await asyncio.sleep(poll_interval_s)
            job = (await s.call("job_poll", "GET", f"/jobs/{job['id']}")).json()
            polls += 1
    except StepFailed:
        s.record(step, time.perf_counter() - t0, False, None, started, {"error": "STEP_FAILED"})
        return None
    summary = job.get("result_summary") or {}
    ok = job["status"] == "succeeded"
    s.record(step, time.perf_counter() - t0, ok, None, started,
             {"mode": summary.get("mode"), "fallback_reason": summary.get("fallback_reason"), "polls": polls,
              "error": (job.get("error") or {}).get("code")})
    return summary if ok else None


async def iteration(s: UserSession, scenario: dict, image: bytes, *, poll_interval_s: float, timeout_s: float):
    meal_date = date.fromisoformat(scenario["base_meal_date"]) + timedelta(days=s.iteration)
    participants = [{"member_id": s.member_id}]
    await s.call("me", "GET", "/auth/me")
    req = (await s.call("questionnaire_create", "POST", "/meal-requests", json={
        **scenario["database_request"], "meal_date": meal_date.isoformat(), "participants": participants})).json()
    await s.call("questionnaire_read", "GET", f"/meal-requests/{req['id']}")

    rec = (await s.call("recognition", "POST", "/recognitions", data={"meal_request_id": req["id"]},
                        files={"image": ("t18.jpg", image, "image/jpeg")})).json()
    s.result.samples[-1].detail["recognition_status"] = rec.get("status")
    if rec.get("status") != "succeeded":
        s.issue("RECOGNITION_NOT_SUCCEEDED", status=rec.get("status"))
    cfg = scenario["recognition"]
    await s.call("recognition_confirm", "POST", f"/recognitions/{rec['id']}/confirm",
                 json={"name": cfg["confirm_name"], "ingredient_id": cfg["confirm_ingredient_id"]})

    prev = (await s.call("previous_day_read", "GET", "/meal-plans",
                         params={"date": (meal_date - timedelta(days=1)).isoformat()})).json()
    s.result.samples[-1].detail["count"] = len(prev.get("meal_plans", []))

    summary = await generate(s, "generation_database", req["id"], req["version"], submit_step="generation_submit",
                             poll_interval_s=poll_interval_s, timeout_s=timeout_s)

    fb = (await s.call("fallback_questionnaire_create", "POST", "/meal-requests", json={
        **scenario["fallback_request"], "meal_date": meal_date.isoformat(), "participants": participants})).json()
    fb_summary = await generate(s, "generation_fallback", fb["id"], fb["version"], submit_step="fallback_submit",
                                poll_interval_s=poll_interval_s, timeout_s=timeout_s)
    if fb_summary is not None and fb_summary.get("mode") != "ai_generated_menu":
        s.issue("FALLBACK_NOT_TRIGGERED", mode=fb_summary.get("mode"))

    if summary is None:
        return
    view = (await s.call("result_view", "GET",
                         f"/meal-requests/{req['id']}/results/{summary['result_version']}")).json()
    eligibility = view.get("eligibility") or {}
    s.result.samples[-1].detail.update({
        "knowledge_status": (view.get("knowledge") or {}).get("status"),
        "previous_day": (view.get("previous_day") or {}).get("status"),
        "adoptable": eligibility.get("adoptable"), "mode": view.get("mode")})
    if not eligibility.get("adoptable"):
        s.issue("NOT_ADOPTABLE", blocks=eligibility.get("blocks"))
    else:
        plan = (await s.call("adoption", "POST", "/meal-plans", json={
            "meal_request_id": req["id"], "result_version": summary["result_version"],
            "request_version": view.get("request_version", req["version"])},
            headers={"Idempotency-Key": f"t18-{s.run_id}-{uuid.uuid4().hex[:12]}"})).json()["meal_plan"]
        await s.call("history_detail", "GET", f"/meal-plans/{plan['id']}")
    await s.call("history_read", "GET", "/meal-plans", params={"date": meal_date.isoformat()})


async def run_load(scenario: dict, *, transport=None, base_url: str, users: int = lr.REQUIRED_USERS,
                   iterations: int = 6, poll_interval_s: float = 1.0, timeout_s: float = 300.0,
                   accounts: list | None = None, image: bytes | None = None,
                   origin: str | None = None) -> LoadResult:
    if users < lr.REQUIRED_USERS:
        raise ValueError(f"AC21 requires at least {lr.REQUIRED_USERS} concurrent users.")
    result = LoadResult()
    run_id = uuid.uuid4().hex[:8]
    password = os.getenv("LOAD_TEST_PASSWORD") or secrets.token_urlsafe(18)
    accounts = accounts or [{"email": f"t18-load-{run_id}-{i}@example.test", "password": password, "register": True}
                            for i in range(users)]
    if len(accounts) < users:
        raise ValueError("Not enough accounts for the requested number of users.")
    image = image or DRY_RUN_IMAGE
    if origin is None:  # 前端與 API 同網域時，Origin 即 base_url 的 scheme://host
        parts = urlsplit(base_url)
        origin = f"{parts.scheme}://{parts.netloc}"
    barrier = asyncio.Barrier(users)
    warmed = asyncio.Event()

    async def one(index: int):
        async with httpx.AsyncClient(transport=transport, base_url=base_url, headers={"Origin": origin},
                                     timeout=httpx.Timeout(timeout_s)) as client:
            s = UserSession(index, client, result, run_id)
            try:
                await setup_user(s, scenario, accounts[index])
            except StepFailed:
                s.issue("SETUP_FAILED")
            # 暖機：第一次辨識不計入 p95（AC21 量測的是暖機後）
            if index == 0 and s.member_id:
                try:
                    await s.call("recognition_warmup", "POST", "/recognitions",
                                 files={"image": ("t18.jpg", image, "image/jpeg")})
                except StepFailed:
                    s.issue("WARMUP_FAILED")
                warmed.set()
            elif index == 0:
                warmed.set()
            await warmed.wait()
            for it in range(iterations):
                s.iteration = it
                await barrier.wait()
                if s.member_id is None:
                    continue
                try:
                    await iteration(s, scenario, image, poll_interval_s=poll_interval_s, timeout_s=timeout_s)
                except StepFailed:
                    pass

    await asyncio.gather(*(one(i) for i in range(users)))
    return result


def _parse_thresholds(values: list[str]) -> dict:
    out = {}
    for item in values or []:
        key, _, value = item.partition("=")
        out[key.strip()] = float(value)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="T18 五人並行負載測試")
    p.add_argument("--base-url", help="實機 API 根網址，例如 https://example.com/api")
    p.add_argument("--dry-run", action="store_true", help="對程序內假伺服器執行（只驗證腳本本身）")
    p.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    p.add_argument("--environment", type=Path, help="環境資料 JSON（欄位見 environment.example.json）")
    p.add_argument("--accounts", type=Path, help="既有測試帳號 JSON：[{email, password}]；未給則自動註冊")
    p.add_argument("--users", type=int, default=lr.REQUIRED_USERS)
    p.add_argument("--iterations", type=int, default=6)
    p.add_argument("--poll-interval", type=float, default=1.0)
    p.add_argument("--generation-timeout", type=float, default=300.0)
    p.add_argument("--threshold", action="append", help="只能收緊，例如 api=0.8；放寬會被拒絕")
    p.add_argument("--origin", help="前端網址（CSRF Origin 檢查用）；預設取 --base-url 的 scheme://host")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)

    try:
        thresholds = lr.effective_thresholds(_parse_thresholds(args.threshold))
    except (lr.ThresholdLoweringRefused, ValueError) as exc:
        print(f"拒絕執行：{exc}", file=sys.stderr)
        return 2
    if not args.dry_run and not args.base_url:
        print("實機執行需要 --base-url（或改用 --dry-run）。", file=sys.stderr)
        return 2
    if args.users < lr.REQUIRED_USERS:
        print(f"AC21 需要至少 {lr.REQUIRED_USERS} 位並行使用者。", file=sys.stderr)
        return 2

    scenario = load_scenario(args.scenario)
    image_paths = [Path(args.scenario).parent / f for f in scenario["recognition"].get("image_files", [])]
    if args.dry_run:
        import fake_server
        transport, base_url, image = asgi_transport(fake_server.create_app()), "https://fake.test", None
        poll = 0.0
    else:
        if not image_paths or not all(f.is_file() for f in image_paths):
            print("實機執行需要 scenario.recognition.image_files 指向實際照片。", file=sys.stderr)
            return 2
        transport, base_url, image, poll = None, args.base_url.rstrip("/"), image_paths[0].read_bytes(), args.poll_interval
    accounts = None
    if args.accounts:
        accounts = [{**a, "register": False} for a in json.loads(args.accounts.read_text(encoding="utf-8"))]

    environment = lr.environment_template()
    if args.environment:
        supplied = json.loads(args.environment.read_text(encoding="utf-8"))
        for section, values in supplied.items():
            if isinstance(values, dict) and section in environment:
                environment[section].update(values)
    environment["deployment"]["base_url"] = base_url
    environment["inputs"]["scenario_file"] = f"{Path(args.scenario).name} sha256={scenario['_sha256']}"
    environment["inputs"]["image_files"] = [
        f"{f.name} sha256={hashlib.sha256(f.read_bytes()).hexdigest()}" for f in image_paths if f.is_file()] or None

    result = asyncio.run(run_load(scenario, transport=transport, base_url=base_url, users=args.users,
                                  iterations=args.iterations, poll_interval_s=poll,
                                  timeout_s=args.generation_timeout, accounts=accounts, image=image,
                                  origin=args.origin))
    report = lr.build_report(result.samples, environment=environment, dry_run=args.dry_run,
                             thresholds=thresholds, users=args.users, issues=result.issues)
    json_path, md_path = lr.write_report(report, result.samples, args.out)
    print(f"G06 verdict: {report['g06_verdict']}\n{json_path}\n{md_path}")
    if args.dry_run:
        return 0
    return 0 if report["g06_verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
