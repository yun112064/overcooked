"""HTTP smoke against the isolated 1111 browser stack."""
from datetime import datetime
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

HERE = Path(__file__).resolve().parents[2] / ".test-runtime/1111_update_V2"
BASE_PORT = 8240
MAIN = f"http://localhost:{BASE_PORT}"
ADMIN = f"http://127.0.0.1:{BASE_PORT + 2}"
MARKET = f"http://127.0.0.1:{BASE_PORT + 1}"
BACKEND = "overcooked-1111_update_v2-backend-1"


def call(client, method, path, *, code=200, **kwargs):
    result = client.request(method, path, **kwargs)
    if result.status_code != code:
        raise RuntimeError(f"{method} {path} returned {result.status_code}: {result.text[:1000]}")
    return result.json()


db = subprocess.check_output(
    ["docker", "exec", BACKEND, "python", "-c",
     "from app.db.session import engine; from sqlalchemy import text; "
     "c=engine.connect(); print(c.scalar(text('SELECT current_database()'))); c.close()"],
    text=True,
).strip()
assert db == "meal_planner_1102_v3_1111_update_v2", db

for url in (MAIN, MARKET, f"http://127.0.0.1:{BASE_PORT + 2}"):
    page = httpx.get(url, trust_env=False, timeout=30)
    assert page.status_code == 200 and 'type="module"' in page.text, (url, page.status_code)
print("Three real site entry points are reachable.", flush=True)

credentials_path = HERE / "test-accounts.json"
if credentials_path.exists():
    accounts = json.loads(credentials_path.read_text(encoding="utf-8"))
else:
    accounts = {}
for role in ("user", "admin", "operator"):
    if role not in accounts:
        accounts[role] = {"email": f"1111-{role}@example.test", "password": secrets.token_urlsafe(14)}
credentials_path.write_text(json.dumps(accounts, indent=2), encoding="utf-8")

main = httpx.Client(base_url=MAIN + "/api", headers={"Origin": MAIN}, trust_env=False, timeout=30)
admin = httpx.Client(base_url=f"http://127.0.0.1:{BASE_PORT + 2}/api",
                     headers={"Origin": ADMIN, "Host": urlsplit(ADMIN).netloc}, trust_env=False, timeout=30)
assert call(main, "GET", "/health/db")["status"] == "ok"
for account in accounts.values():
    # An existing local acceptance account can use a provisioned login name.
    # Registration still requires an email; do not re-register an existing user.
    if main.post("/auth/login", json=account).status_code == 200:
        continue
    response = main.post("/auth/register", json=account)
    assert response.status_code in (201, 409), response.text
    if account == accounts["admin"] and response.status_code == 201:
        subprocess.run(["docker", "exec", BACKEND, "python", "-m",
                        "app.management.bootstrap", "--user-id", response.json()["user"]["id"],
                        "--reason", "Local 1111 isolated acceptance"],
                       check=True, stdout=subprocess.DEVNULL)

call(admin, "POST", "/auth/login", json=accounts["admin"])
capabilities = call(admin, "GET", "/admin/v1/capabilities")
operator = call(main, "POST", "/auth/login", json=accounts["operator"])["user"]["id"]
grants = call(admin, "GET", "/admin/v1/role-grants", params={"user_id": operator})
if not grants["items"]:
    call(admin, "POST", "/admin/v1/role-grants", code=201, headers={"Idempotency-Key": uuid4().hex}, json={
        "user_id": operator, "role": "operator", "domain_scope": grants["domains"], "expires_at": None,
        "expected_access_revision": capabilities["access_revision"], "reason": "1111 isolated acceptance",
    })
call(admin, "POST", "/auth/login", json=accounts["operator"])
initial = call(admin, "GET", "/admin/v1/meal-confirmation-flows")["items"]
print("Admin authenticated with the non-owner application role.", flush=True)
call(main, "POST", "/auth/login", json=accounts["user"])
assert main.get("/admin/v1/capabilities").status_code == 404
if not call(main, "GET", "/household")["current_household_id"]:
    call(main, "POST", "/household", code=201, json={"name": "1111 隔離驗收家庭"})
members = call(main, "GET", "/members")["members"]
if not members:
    for name, sex, weight in [("驗收成員 A", "M", "70"), ("驗收成員 B", "F", "55")]:
        call(main, "POST", "/members", code=201, json={
            "display_name": name, "birth_date": "1990-05-01", "equation_sex": sex,
            "weight_kg": weight, "activity_category": "sedentary", "goal": "maintain", "portion": "1",
        })
    members = call(main, "GET", "/members")["members"]

print("Isolated user/admin/operator accounts and acceptance household ready; credentials saved locally.", flush=True)
