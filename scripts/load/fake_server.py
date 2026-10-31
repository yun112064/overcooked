"""T18 dry-run 用的本機假 API：只模擬負載腳本走到的路徑與回應形狀，不含任何業務邏輯。

僅供測試負載腳本本身（流程、計時、報告），量到的數字不能當作驗收證據。
以 httpx.ASGITransport 在同一程序內執行，不開連接埠、不連任何外部服務。
"""
import asyncio
import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse

# 假資料庫裡「有」的菜；指定其他菜時模擬資料庫無解 → 自動 AI 回退
KNOWN_DISHES = {"番茄炒蛋", "炒高麗菜", "蒜炒青江菜"}


@dataclass
class FakeState:
    delay_s: float
    polls_until_done: int
    fallback_works: bool
    fail_generation: bool
    in_flight: int = 0
    max_in_flight: int = 0
    users: dict = field(default_factory=dict)
    requests: dict = field(default_factory=dict)
    jobs: dict = field(default_factory=dict)
    plans: dict = field(default_factory=dict)


def create_app(*, delay_s: float = 0.0, polls_until_done: int = 1, fallback_works: bool = True,
               fail_generation: bool = False) -> FastAPI:
    app = FastAPI()
    st = FakeState(delay_s, polls_until_done, fallback_works, fail_generation)
    app.state.fake = st

    @app.middleware("http")
    async def track(request: Request, call_next):
        st.in_flight += 1
        st.max_in_flight = max(st.max_in_flight, st.in_flight)
        try:
            if st.delay_s:
                await asyncio.sleep(st.delay_s)
            return await call_next(request)
        finally:
            st.in_flight -= 1

    def user_of(request: Request) -> dict | None:
        return st.users.get(request.cookies.get("session"))

    def unauth():
        return JSONResponse({"error": {"code": "UNAUTHENTICATED", "message": "", "details": {}}}, status_code=401)

    def login_response(response: Response, email: str) -> dict:
        token = uuid.uuid4().hex
        st.users[token] = {"email": email, "household": None}
        response.set_cookie("session", token, secure=True, httponly=True)
        return {"user": {"id": token, "email": email}, "current_household": None}

    @app.post("/auth/register", status_code=201)
    async def register(body: dict, response: Response):
        return login_response(response, body["email"])

    @app.post("/auth/login")
    async def login(body: dict, response: Response):
        return login_response(response, body["email"])

    @app.get("/auth/me")
    async def me(request: Request):
        u = user_of(request)
        return unauth() if u is None else {"user": {"email": u["email"]}, "current_household": u["household"]}

    @app.get("/household")
    async def households(request: Request):
        u = user_of(request)
        if u is None:
            return unauth()
        return {"households": [u["household"]] if u["household"] else [],
                "current_household_id": u["household"]["id"] if u["household"] else None}

    @app.post("/household", status_code=201)
    async def create_household(body: dict, request: Request):
        u = user_of(request)
        u["household"] = {"id": str(uuid.uuid4()), "name": body["name"]}
        return u["household"]

    @app.post("/members", status_code=201)
    async def create_member(body: dict):
        return {"id": str(uuid.uuid4()), **body}

    @app.post("/meal-requests", status_code=201)
    async def create_request(body: dict):
        rid = str(uuid.uuid4())
        st.requests[rid] = {"id": rid, "version": 1, "payload": body, "status": "draft", "results": {}}
        return {"id": rid, "version": 1, "status": "draft", "payload": body}

    @app.get("/meal-requests/{rid}")
    async def get_request(rid: str):
        r = st.requests[rid]
        return {"id": rid, "version": r["version"], "status": r["status"], "payload": r["payload"]}

    @app.post("/recognitions", status_code=201)
    async def recognize(image: UploadFile = File(...), meal_request_id: str | None = Form(None)):
        await image.read()
        return {"id": str(uuid.uuid4()), "status": "succeeded", "candidates": [{"label": "cabbage"}],
                "confirmed_items": []}

    @app.post("/recognitions/{rec_id}/confirm")
    async def confirm(rec_id: str, body: dict):
        return {"id": rec_id, "status": "succeeded", "confirmed_items": [body]}

    @app.get("/meal-plans")
    async def plans(date: str | None = None):
        return {"meal_date": date, "meal_plans": [p for p in st.plans.values() if p["meal_date"] == date]}

    @app.get("/meal-plans/{plan_id}")
    async def plan(plan_id: str):
        return st.plans[plan_id]

    @app.post("/meal-requests/{rid}/submit", status_code=202)
    async def submit(rid: str, body: dict):
        r = st.requests[rid]
        dishes = r["payload"].get("specified_dishes") or []
        fallback = any(d not in KNOWN_DISHES for d in dishes) and st.fallback_works
        jid = str(uuid.uuid4())
        st.jobs[jid] = {"rid": rid, "polls": 0, "mode": "ai_generated_menu" if fallback else "database_menu"}
        return {"job": {"id": jid, "status": "queued", "stage": "queued"}, "created": True}

    @app.get("/jobs/{jid}")
    async def job(jid: str):
        j = st.jobs[jid]
        j["polls"] += 1
        if j["polls"] < st.polls_until_done:
            return {"id": jid, "status": "running" if j["polls"] > 1 else "queued", "result_summary": None}
        if st.fail_generation:
            return {"id": jid, "status": "failed", "error": {"code": "GENERATION_FAILED", "message": ""},
                    "result_summary": None}
        r = st.requests[j["rid"]]
        r["results"][1] = j["mode"]
        return {"id": jid, "status": "succeeded",
                "result_summary": {"result_version": 1, "mode": j["mode"],
                                   "fallback_reason": "NO_FEASIBLE_DATABASE_MENU" if j["mode"] != "database_menu" else None}}

    @app.get("/meal-requests/{rid}/results/{version}")
    async def result(rid: str, version: int):
        r = st.requests[rid]
        return {"meal_request_id": rid, "result_version": version, "request_version": r["version"],
                "mode": r["results"][version], "knowledge": {"status": "found", "items": []},
                "previous_day": {"status": "none"}, "eligibility": {"adoptable": True, "blocks": []}}

    @app.post("/meal-plans")
    async def adopt(body: dict, response: Response):
        r = st.requests[body["meal_request_id"]]
        pid = str(uuid.uuid4())
        st.plans[pid] = {"id": pid, "meal_date": r["payload"]["meal_date"], "meal_request_id": r["id"]}
        response.status_code = 201
        return {"meal_plan": st.plans[pid], "created": True}

    return app
