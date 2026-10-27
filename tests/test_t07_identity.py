"""T07：登入、家庭及成員設定（AC01、AC02）。成員資料皆為合成測試資料。"""
import unittest
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from db_support import seed_foods, ORIGIN, make_client, migrate_fresh, requires_db, session_factory, truncate_all

PASSWORD = "correct-horse-42"


def adult(**extra):
    body = {
        "display_name": "成員A",
        "birth_date": "1996-03-01",
        "equation_sex": "M",
        "weight_kg": "70",
        "activity_category": "low_active",
        "goal": "maintain",
        "portion": "1",
        "allergen_ingredient_ids": [],
        "avoided_ingredient_ids": [],
    }
    body.update(extra)
    return body


def years_ago(years: int, days: int = 0) -> str:
    today = datetime.now(timezone.utc).date()
    return (date(today.year - years, today.month, min(today.day, 28)) + timedelta(days=days)).isoformat()


class AgeTests(unittest.TestCase):
    def test_age_counts_full_years_only(self):
        from app.services.household import age_on

        self.assertEqual(age_on(date(2007, 10, 19), date(2026, 10, 18)), 18)
        self.assertEqual(age_on(date(2007, 10, 18), date(2026, 10, 18)), 19)
        self.assertEqual(age_on(date(2008, 2, 29), date(2026, 2, 28)), 17)
        self.assertEqual(age_on(date(2008, 2, 29), date(2026, 3, 1)), 18)


@requires_db
class T07ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        seed_foods()
        self.client = make_client()

    def register(self, client=None, email="owner@example.test"):
        client = client or self.client
        res = client.post("/auth/register", json={"email": email, "password": PASSWORD})
        self.assertEqual(res.status_code, 201, res.text)
        return res

    def with_household(self, client=None, email="owner@example.test", name="測試家庭"):
        client = client or self.client
        self.register(client, email)
        res = client.post("/household", json={"name": name})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["id"]

    def error(self, res):
        return res.json()["error"]

    # --- 帳號與 session ---

    def test_register_sets_secure_httponly_cookie_and_hashes_password(self):
        res = self.register()
        cookie = res.headers["set-cookie"].lower()
        for flag in ("httponly", "secure", "samesite=lax", "path=/"):
            self.assertIn(flag, cookie)
        self.assertEqual(self.client.get("/auth/me").json()["user"]["email"], "owner@example.test")

        from app.db.models import User, UserSession

        with session_factory()() as db:
            user = db.scalar(select(User))
            self.assertTrue(user.password_hash.startswith("$argon2"))
            self.assertNotIn(PASSWORD, user.password_hash)
            token = self.client.cookies.get("mp_session")
            stored = db.scalar(select(UserSession.token_hash))
            self.assertNotEqual(stored, token)

    def test_register_rejects_duplicate_email_and_short_password(self):
        self.register()
        dup = make_client().post("/auth/register", json={"email": "OWNER@example.test", "password": PASSWORD})
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(self.error(dup)["details"]["reason"], "EMAIL_TAKEN")
        short = make_client().post("/auth/register", json={"email": "x@example.test", "password": "pw7Qz"})
        self.assertEqual(short.status_code, 422)
        self.assertEqual(self.error(short)["code"], "INVALID_INPUT")
        self.assertNotIn("pw7Qz", short.text)

    def test_login_logout_and_wrong_password(self):
        self.register()
        other = make_client()
        bad = other.post("/auth/login", json={"email": "owner@example.test", "password": "wrong-password"})
        self.assertEqual(bad.status_code, 401)
        unknown = other.post("/auth/login", json={"email": "nobody@example.test", "password": PASSWORD})
        self.assertEqual(unknown.json(), bad.json())
        ok = other.post("/auth/login", json={"email": "owner@example.test", "password": PASSWORD})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(other.get("/auth/me").status_code, 200)
        self.assertEqual(other.post("/auth/logout").status_code, 204)
        self.assertEqual(other.get("/auth/me").status_code, 401)
        # 另一個 session 不受影響
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

    def test_login_selects_household_only_when_unambiguous(self):
        first = self.with_household(name="家庭一")
        def login():
            client = make_client()
            res = client.post("/auth/login", json={"email": "owner@example.test", "password": PASSWORD})
            self.assertEqual(res.status_code, 200)
            return res.json()["current_household"], client
        current, client = login()
        self.assertEqual(current["id"], first)
        self.assertEqual(client.get("/members").status_code, 200)
        self.client.post("/household", json={"name": "家庭二"})
        current, client = login()
        self.assertIsNone(current)
        self.assertEqual(client.get("/members").status_code, 409)

    def test_expired_session_is_rejected(self):
        self.register()
        from app.db.models import UserSession

        with session_factory()() as db:
            row = db.scalar(select(UserSession))
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_state_changes_require_allowed_origin(self):
        from fastapi.testclient import TestClient
        from app.main import app

        bare = TestClient(app, base_url="https://testserver")
        res = bare.post("/auth/register", json={"email": "a@example.test", "password": PASSWORD})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.error(res)["details"]["reason"], "ORIGIN_NOT_ALLOWED")
        evil = bare.post("/auth/register", json={"email": "a@example.test", "password": PASSWORD},
                         headers={"Origin": "https://evil.example"})
        self.assertEqual(evil.status_code, 403)
        via_referer = bare.post("/auth/register", json={"email": "a@example.test", "password": PASSWORD},
                                headers={"Referer": ORIGIN + "/register"})
        self.assertEqual(via_referer.status_code, 201)

    # --- 家庭 ---

    def test_household_create_select_and_isolation(self):
        first = self.with_household(name="家庭一")
        second = self.client.post("/household", json={"name": "家庭二"}).json()["id"]
        listed = self.client.get("/household").json()
        self.assertEqual([h["name"] for h in listed["households"]], ["家庭一", "家庭二"])
        self.assertEqual(listed["current_household_id"], second)
        self.assertEqual(self.client.post("/household/select", json={"household_id": first}).status_code, 200)
        self.assertEqual(self.client.get("/auth/me").json()["current_household"]["id"], first)

        intruder = make_client()
        self.with_household(intruder, "intruder@example.test", "別人的家")
        res = intruder.post("/household/select", json={"household_id": first})
        self.assertEqual(res.status_code, 404)
        self.assertNotIn("家庭一", intruder.get("/household").text)

    def test_members_require_selected_household(self):
        self.register()
        res = self.client.get("/members")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.error(res)["code"], "HOUSEHOLD_NOT_SELECTED")

    # --- 成員 ---

    def test_member_crud_with_restrictions_and_versioning(self):
        self.with_household()
        res = self.client.post("/members", json=adult(
            allergen_ingredient_ids=["FOOD001566"], avoided_ingredient_ids=["FOOD000039", "FOOD000039"]))
        self.assertEqual(res.status_code, 201, res.text)
        member = res.json()
        self.assertEqual(member["portion"], "1")
        # 資料庫 Numeric(6,2) 的 70.00 不可顯示成科學記號 7E+1
        self.assertEqual(member["weight_kg"], "70")
        heavy = self.client.post("/members", json=adult(display_name="成員C", weight_kg="100.50")).json()
        self.assertEqual(heavy["weight_kg"], "100.5")
        self.client.delete(f"/members/{heavy['id']}")
        self.assertEqual(member["allergens"], [{"ingredient_id": "FOOD001566", "name": "雞蛋"}])
        self.assertEqual([a["ingredient_id"] for a in member["avoided"]], ["FOOD000039"])

        update = self.client.put(f"/members/{member['id']}", json=adult(portion="1.3", version=1))
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(update.json()["portion"], "1.3")
        self.assertEqual(update.json()["version"], 2)
        stale = self.client.put(f"/members/{member['id']}", json=adult(portion="0.7", version=1))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.error(stale)["code"], "VERSION_CONFLICT")

        reread = self.client.get(f"/members/{member['id']}").json()
        self.assertEqual((reread["portion"], reread["version"]), ("1.3", 2))
        self.assertEqual(self.client.delete(f"/members/{member['id']}").status_code, 204)
        self.assertEqual(self.client.get("/members").json()["members"], [])

    def test_only_five_portions_are_accepted(self):
        self.with_household()
        for n in ("0.7", "0.85", "1", "1.15", "1.3", "1.00"):
            self.assertEqual(self.client.post("/members", json=adult(portion=n)).status_code, 201, n)
        for n in ("0.9", "2", "0", "-1", "1.2"):
            res = self.client.post("/members", json=adult(portion=n))
            self.assertEqual(res.status_code, 422, n)
            self.assertEqual(self.error(res)["details"].get("reason"), "INVALID_PORTION", n)

    def test_minor_cannot_use_goal_adjustments(self):
        self.with_household()
        minor = years_ago(12)
        for goal in ("maintain", "muscle_gain", "fat_loss"):
            res = self.client.post("/members", json=adult(birth_date=minor, weight_kg="40", goal=goal))
            self.assertEqual(res.status_code, 422, goal)
            self.assertEqual(self.error(res)["details"]["reason"], "GOAL_NOT_ALLOWED_FOR_AGE")
            self.assertEqual(self.error(res)["details"]["field"], "goal")
        ok = self.client.post("/members", json=adult(birth_date=minor, weight_kg="40", goal="general"))
        self.assertEqual(ok.status_code, 201, ok.text)
        adult_19 = self.client.post("/members", json=adult(birth_date=years_ago(19, days=-1), goal="muscle_gain"))
        self.assertEqual(adult_19.status_code, 201, adult_19.text)

    def test_fat_loss_below_minimum_is_reported_not_clamped(self):
        self.with_household()
        res = self.client.post("/members", json=adult(
            birth_date=years_ago(30), weight_kg="55", activity_category="sedentary", goal="fat_loss"))
        self.assertEqual(res.status_code, 422)
        self.assertEqual(self.error(res)["details"]["reason"], "DAILY_ENERGY_BELOW_MINIMUM")
        self.assertEqual(self.client.get("/members").json()["members"], [])

    def test_invalid_member_inputs_are_rejected(self):
        self.with_household()
        cases = [
            adult(allergen_ingredient_ids=["FOOD999999"]),
            adult(birth_date="2999-01-01"),
            adult(weight_kg="0"),
            adult(equation_sex="X"),
            adult(activity_category="couch"),
            adult(display_name="   "),
        ]
        for body in cases:
            res = self.client.post("/members", json=body)
            self.assertEqual(res.status_code, 422, body)
            self.assertEqual(self.error(res)["code"], "INVALID_INPUT")

    def test_member_target_uses_versioned_rule_without_portion(self):
        self.with_household()
        member = self.client.post("/members", json=adult(birth_date=years_ago(30), portion="1.3")).json()
        target = self.client.get(f"/members/{member['id']}/target").json()
        self.assertEqual(target["rule_version"], "hpa-dri8-mvp-0.4.0")
        self.assertEqual(target["daily_reference"]["protein_g"], "77.0")
        self.assertFalse(target["approved_for_adoption"])

    def test_cross_household_member_access_is_denied(self):
        self.with_household()
        member = self.client.post("/members", json=adult()).json()
        intruder = make_client()
        self.with_household(intruder, "intruder@example.test", "別人的家")
        for method, kwargs in (("get", {}), ("put", {"json": adult(version=1)}), ("delete", {})):
            res = getattr(intruder, method)(f"/members/{member['id']}", **kwargs)
            self.assertEqual(res.status_code, 404, method)
        self.assertEqual(intruder.get(f"/members/{member['id']}/target").status_code, 404)
        self.assertEqual(intruder.get("/members").json()["members"], [])
        self.assertEqual(self.client.get(f"/members/{member['id']}").status_code, 200)

    def test_options_and_ingredient_search_require_login(self):
        anon = make_client()
        self.assertEqual(anon.get("/ingredients", params={"q": "醬"}).status_code, 401)
        self.register()
        found = self.client.get("/ingredients", params={"q": "醬油"}).json()["ingredients"]
        self.assertEqual(found[0], {"ingredient_id": "FOOD001541", "name": "醬油"})
        options = self.client.get("/members/options").json()
        self.assertEqual(options["portions"], ["0.7", "0.85", "1", "1.15", "1.3"])
        self.assertEqual(options["minor_goals"], ["general"])


if __name__ == "__main__":
    unittest.main()
