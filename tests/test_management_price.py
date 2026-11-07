"""T02 management gates with synthetic observations and an isolated PostgreSQL workflow."""
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.errors import ApiError, register_error_handlers
from app.api.management_price import router, service
from app.core.config import settings
from app.db.models import AdoptedMealPlan, Household, User
from app.meal_calc.types import IngredientRequirement
from app.management.contracts import rules_pin, validate
from app.management.active_price_policy import price_readiness
from app.management.models import tables as T
from app.management.price_policy import ManagedPrice, list_observations, preview_price, utc_stamp
from app.management.service import Management, legacy_capture
from app.models.recipe import IngredientAlias, RecipeDataRelease
from app.services.meal_cost import meal_cost_from_db
from app.source_adapters.price_fixture import collect_fixture
from app.source_adapters.price_import import install
from app.source_adapters.price_models import IngredientQuery, PriceQuote
from app.source_adapters.pricing_repository import PricingRepository, RunLease
from test_management_foundation import ACTORS, Environment
from test_t02_release import FOOD, NAME, OLD, synthetic_bundle

RELEASE = "legacy-1017"
AT = datetime.now(timezone.utc)
OBSERVED = AT - timedelta(hours=1)
SKU = "SYNTHETIC"
SKU_NAME = NAME + " 300g"


def correction(**overrides):
    return {
        "correction_kind": "quote_correction", "food_id": FOOD, "ingredient_version_id": "dictionary-food",
        "ingredient_publication_id": "dictionary-v1", "legacy_recipe_release_id": RELEASE,
        "provider": "pxmart", "product_id": SKU, "product_name": SKU_NAME, "snapshot_id": None,
        "price_twd": "73.25", "package_mass_g": "300", "observed_at": utc_stamp(OBSERVED),
        "valid_from": utc_stamp(OBSERVED), "valid_until": utc_stamp(OBSERVED + timedelta(days=3)),
        "availability": "in_stock", "region_condition": None, "store_condition": None,
        "promotion_condition": None, "sources": [], **overrides,
    }


def binding(**overrides):
    return correction(
        **{"correction_kind": "product_binding", "price_twd": None, "package_mass_g": None,
           "observed_at": None, "valid_from": None, "valid_until": None, "availability": "unknown", **overrides}
    )


def quote(**overrides):
    return PriceQuote.model_validate({
        **synthetic_bundle().quotes[0]["quote_payload"], "queried_at": AT,
        "recipe_release_id": RELEASE, "snapshot_id": 9, **overrides,
    })


def preview(entries, observations, fallback, *, as_of=AT):
    return preview_price(entries, observations, fallback, food_id=FOOD, release_id=RELEASE,
                         dictionary_id="dictionary-v1", ingredient_version_id="dictionary-food", as_of=as_of)


def test_new_fixture_sku_does_not_replace_reviewed_quote_or_locked_binding():
    later = quote(product_id="cheaper", product_name="Another SKU", price="12")
    reviewed = ManagedPrice("manual-version", "review", correction())
    selected = preview([reviewed], [("pxmart", later)], later).quote
    assert selected.selection_tier.value == "manual_approved"
    assert (selected.price, selected.package_quantity) == (Decimal("73.25"), Decimal("300"))
    assert (selected.manual_revision_id, selected.review_decision_id) == ("manual-version", "review")
    assert selected.valid_until == OBSERVED + timedelta(hours=48)
    assert "manual_revision_id" not in later.model_dump()
    with pytest.raises(ValidationError):
        PriceQuote.model_validate({**selected.model_dump(), "manual_revision_id": None})

    lock = ManagedPrice("binding", "review", binding())
    assert preview([lock], [("pxmart", later)], later).reason == "PRICE_LOCKED_SKU_UNAVAILABLE"
    original = quote(price="60", queried_at=OBSERVED)
    assert preview([lock], [("pxmart", original), ("pxmart", later)], later).quote == original


def test_unknown_context_expiry_and_ml_block_fallback():
    fallback = quote()
    conditional = ManagedPrice("manual-version", "review", correction(store_condition="Store A"))
    assert preview([conditional], [], fallback).reason == "PRICE_CONTEXT_REQUIRED"
    reviewed = ManagedPrice("manual-version", "review", correction())
    assert preview([reviewed], [], fallback, as_of=OBSERVED + timedelta(hours=48)).reason == "PRICE_EXPIRED"
    other = ManagedPrice("other-provider-version", "review", correction(provider="uniprosperity"))
    assert preview([reviewed, other], [], fallback).reason == "PRICE_LOCKED_PROVIDER_REQUIRED"
    lock = ManagedPrice("binding", "review", binding())
    assert preview([lock], [("pxmart", quote(package_unit="ml"))], fallback).reason == "UNIT_MISMATCH"
    assert preview([lock], [("pxmart", quote(usage_status="historical_requires_refresh"))], fallback).reason == "PRICE_REVALIDATION_REQUIRED"
    short_lived = ManagedPrice("binding", "review", binding(), timedelta(minutes=30))
    assert preview([short_lived], [("pxmart", quote(queried_at=OBSERVED))], fallback).reason == "PRICE_EXPIRED"


def test_price_readiness_bounds_visible_warnings():
    moment = AT + timedelta(days=3)
    entries = {str(index): [ManagedPrice("expired", "review", correction())] for index in range(1001)}
    entries["current"] = [ManagedPrice("current", "review", correction(
        observed_at=utc_stamp(moment - timedelta(hours=1)), valid_from=utc_stamp(moment - timedelta(hours=1)),
        valid_until=utc_stamp(moment + timedelta(hours=1)),
    ))]
    issue, warnings = price_readiness(entries, moment)
    assert issue is None and len(warnings) == 999
    assert warnings[-1]["code"] == "PRICE_READINESS_WARNINGS_TRUNCATED"


@pytest.mark.parametrize(("provider", "markup"), [
    ("pxmart", """<script type="application/ld+json">{"@type":"Product","productID":"123",
       "name":"高麗菜 300g","url":"https://fixture.invalid/123","offers":{"price":"60","priceCurrency":"TWD",
       "availability":"https://schema.org/InStock"}}</script>"""),
    ("uniprosperity", """<a class="gtm-product-alink" data-pid="123" data-name="高麗菜 300g"
        data-price="60" data-ifavailable="true"></a>"""),
])
def test_fixture_parses_both_retailers_with_explicit_time(provider, markup):
    query = IngredientQuery(canonical_name="高麗菜", food_ids=[FOOD])
    html = markup.encode()
    first = collect_fixture(provider, query, html, observed_at=OBSERVED)
    assert first == collect_fixture(provider, query, html, observed_at=OBSERVED)
    assert first["execution_mode"] == "fixtures_only"
    data = first["observation"]
    assert (data["status"], data["price"], data["package_quantity"], data["queried_at"]) == (
        "success", "60", "300", utc_stamp(OBSERVED),
    )
    failed = collect_fixture(provider, query, b"<html></html>", observed_at=OBSERVED)["observation"]
    assert failed["status"] == "schema_changed" and failed["price"] is None


class PriceWorkflow(Environment):
    def call(self, actor, method, *args, **kwargs):
        with Session(bind=self.engine, join_transaction_mode="create_savepoint") as db, db.begin():
            return getattr(Management(db, actor), method)(*args, **kwargs)

    def content(self, domain, content, pins):
        artifact = self.call(ACTORS[1], "upload", self.key(), domain, "manual_entry", "text/plain",
                             b"Distinct synthetic source evidence, no real price", "fixture")
        return self.call(ACTORS[1], "create", self.key(), {
            "domain": domain, "content": {**content, "sources": [{
                "artifact_id": artifact["artifact_id"], "artifact_sha256": artifact["sha256"],
                "origin_kind": "manual_entry", "source_policy_version_id": None,
            }]}, "dependency_pins": pins, "reason": "fixture proposal",
        })


def save_fixture_observation(env, run_key, observation):
    conn = env.engine
    run_id = conn.scalar(sa.text("""
        INSERT INTO price_collection_runs (id,run_key,provider,trigger_type,scheduled_for,started_at,status,recipe_release_id)
        VALUES (gen_random_uuid(), :key, 'pxmart', 'manual', :now, :now, 'completed', :release) RETURNING id
    """), {"key": run_key, "now": AT, "release": RELEASE})
    sink = SimpleNamespace(execute=conn.execute, commit=lambda: None)
    PricingRepository(conn.engine).save_quote(
        RunLease(run_id, run_key, sink, recipe_release_id=RELEASE), observation,
    )


@pytest.fixture
def price_env():
    url = os.getenv("T02_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated T02 disposable database not configured")
    engine = sa.create_engine(url)
    assert (engine.url.host, engine.url.port, engine.url.database) == (
        "127.0.0.1", 55542, "meal_planner_1102_v3_t02_test",
    )
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            assert connection.scalar(sa.text("SELECT current_database()")) == engine.url.database
            assert connection.scalar(sa.text(
                "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"
            )) == "overcooked-stage0:T02_V4:20261107"
            connection.execute(sa.text("UPDATE recipe_data_releases SET active = false WHERE active"))
            connection.execute(sa.text("UPDATE recipe_data_releases SET active = true WHERE release_id = :id"), {"id": RELEASE})
            connection.execute(IngredientAlias.__table__.insert(), {"food_id": FOOD, "canonical_name": NAME})
            connection.execute(IngredientAlias.__table__.insert(), {"food_id": "FOOD_SECOND_FIXTURE", "canonical_name": "合成第二食材"})
            connection.execute(User.__table__.insert(), [
                {"id": uuid.UUID(actor), "email": f"price-fixture-{i}@example.invalid", "password_hash": "fixture"}
                for i, actor in enumerate(ACTORS)
            ])
            connection.execute(sa.text(
                "INSERT INTO management_access_state (id, revision) VALUES ('global', 1) ON CONFLICT (id) DO NOTHING"
            ))
            env = PriceWorkflow(connection)
            env.call(ACTORS[0], "bootstrap", "synthetic offline bootstrap")
            env.grants(ACTORS[1], ["price_editor", "ingredient_editor"], ["price", "ingredient"])
            env.grants(ACTORS[2], ["price_reviewer", "ingredient_reviewer", "source_reviewer", "publisher"], ["price", "ingredient"])
            with Session(bind=connection) as db:
                release = db.get(RecipeDataRelease, RELEASE)
                pins = {
                    "legacy_recipe_release_id": RELEASE, "legacy_recipe_source_sha256": release.source_sha256,
                    "legacy_db_capture_sha256": legacy_capture(db), "base_publication_id": None,
                    "base_publication_manifest_sha256": None, "ingredient_publication_id": None,
                    "ingredient_manifest_sha256": None, "source_policy_publication_id": None,
                    "source_policy_manifest_sha256": None, "contract_version": "management-v1.0.0-rc1",
                    **rules_pin(),
                }
            ingredient = env.content("ingredient", {
                "food_id": FOOD, "canonical_name": NAME, "aliases": [], "declared_allergen_ids": [],
            }, pins)
            second_ingredient = env.content("ingredient", {
                "food_id": "FOOD_SECOND_FIXTURE", "canonical_name": "合成第二食材", "aliases": [], "declared_allergen_ids": None,
            }, pins)
            request = env.approved(ingredient)
            request["proposal_refs"].extend(env.approved(second_ingredient)["proposal_refs"])
            publication = env.call(ACTORS[2], "publish", env.key(), request)
            dictionary_id = publication["publication_id"]
            member = connection.execute(sa.select(T["publication_members"]).where(
                T["publication_members"].c.publication_id == dictionary_id,
                T["publication_members"].c.logical_id == FOOD,
            )).mappings().one()
            yield env, {
                **pins, "ingredient_publication_id": dictionary_id,
                "ingredient_manifest_sha256": publication["manifest_hash"],
            }, {"ingredient_version_id": member["version_id"], "ingredient_publication_id": dictionary_id}
            transaction.rollback()
    finally:
        engine.dispose()


def test_pg_price_review_full_membership_overlap_is_atomic(price_env):
    env, pins, identity = price_env
    first = env.content("price", correction(**identity), pins)
    first_pub = env.call(ACTORS[2], "publish", env.key(), env.approved(first))
    first_id = first_pub["publication_id"]
    # The actual publish held the lock through the surrounding transaction.
    with env.engine.engine.connect() as competing:
        assert competing.scalar(sa.text(
            "SELECT pg_try_advisory_xact_lock(hashtextextended('management:publish:price', 0))"
        )) is False
    tables = T["publications"], T["entity_versions"], T["audit_events"]
    before = [env.engine.scalar(sa.select(sa.func.count()).select_from(table)) for table in tables]
    second = env.content("price", correction(**identity, price_twd="82"), {
        **pins, "base_publication_id": first_id, "base_publication_manifest_sha256": first_pub["manifest_hash"],
    })
    request = {**env.approved(second), "base_publication_id": first_id}
    audit_before = env.engine.scalar(sa.select(sa.func.count()).select_from(T["audit_events"]))
    with pytest.raises(ApiError) as error:
        env.call(ACTORS[2], "publish", env.key(), request)
    assert error.value.code == "PRICE_WINDOW_OVERLAP"
    assert [env.engine.scalar(sa.select(sa.func.count()).select_from(table)) for table in tables] == [
        *before[:2], audit_before,
    ]
    assert env.call(ACTORS[1], "get", second["proposal_id"])["status"] == "approved"
    published = env.engine.execute(sa.select(T["entity_versions"].c.content).where(
        T["entity_versions"].c.domain == "price"
    )).scalar_one()
    assert published["price_twd"] == "73.25" and not first_pub["formal_generation_ready"]


def test_pg_historical_price_view_is_raw_but_unusable_and_snapshot_time_immutable(price_env):
    env, pins, identity = price_env
    assert install(env.engine, synthetic_bundle())["status"] == "installed"
    with Session(bind=env.engine) as db:
        result = validate("PriceObservationCollection", list_observations(db, RELEASE, FOOD, AT))
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert "product_name" not in item
    with Session(bind=env.engine) as db:
        extended = list_observations(db, RELEASE, FOOD, AT, include_product_name=True)
    assert extended["items"][0]["product_name"] == synthetic_bundle().quotes[0]["product_name"]
    assert (item["raw_price_twd"], item["queried_at"], item["calculation_usable"], item["missing_reason"]) == (
        "60", utc_stamp(OLD), False, "PRICE_REVALIDATION_REQUIRED",
    )
    candidate = env.content("price", correction(
        **identity, snapshot_id=item["snapshot_id"], observed_at=utc_stamp(OLD), valid_from=utc_stamp(OLD),
    ), pins)
    receipt = env.call(ACTORS[1], "request_validation", env.key(), candidate["proposal_id"], env.expected(candidate))
    validation = env.call(ACTORS[1], "run_validation", receipt["task_id"])
    assert {issue["code"] for issue in validation["issues"]} >= {"PRICE_NEW_OBSERVATION_REQUIRED", "PRICE_EXPIRED"}
    assert env.engine.scalar(sa.text("SELECT queried_at FROM price_quote_snapshots")) == OLD


    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    actor = ACTORS[3]

    def request_service():
        with Session(bind=env.engine) as db:
            yield Management(db, actor)

    app.dependency_overrides[service] = request_service
    with TestClient(app) as client:
        path = "/admin/v1/price-observations"
        params = {"recipe_release_id": RELEASE, "food_id": FOOD, "as_of": utc_stamp(AT)}
        assert client.get(path, params=params).status_code == 403
        assert client.get("/admin/v1/contracts/price-v1").status_code == 403
        actor = ACTORS[1]
        assert client.get(path, params={**params, "as_of": "2026-02-31T00:00:00Z"}).status_code == 422
        assert client.get(path, params=params).json() == result
        contract = client.get("/admin/v1/contracts/price-v1").json()
        assert contract["extension"]["extension_version"] == "management-price-v1"
        with_name = client.get(path, params={**params, "include_product_name": "true"}).json()
        from jsonschema import Draft202012Validator
        schema = {"$defs": contract["schemas"]["$defs"], "$ref": "#/$defs/PriceObservationCollection"}
        Draft202012Validator(schema).validate(with_name)
        assert with_name == extended


def test_pg_t13_activation_replacement_and_rollback_keep_adopted_history(price_env, monkeypatch):
    env, pins, identity = price_env
    conn = env.engine
    publisher = ACTORS[2]
    household, plan_id = uuid.uuid4(), uuid.uuid4()
    conn.execute(Household.__table__.insert().values(id=household, name="Synthetic household"))
    conn.execute(AdoptedMealPlan.__table__.insert().values(
        id=plan_id, household_id=household, meal_date=date(2026, 1, 1), meal_type="dinner",
        mode="database_menu", status="adopted", version=1,
        dishes=[{"recipe_id": "old-recipe", "dish_name": "原菜名"}],
        snapshot={"package_spend": "60", "proportional_cost": "20", "unknown": None},
    ))
    snapshot = sa.text("SELECT convert_to(row_to_json(p)::text, 'UTF8') FROM adopted_meal_plans p WHERE id=:id")
    before = bytes(conn.scalar(snapshot, {"id": plan_id}))
    save_fixture_observation(env, "later-fixture",
                             quote(product_id="new-cheaper-sku", product_name="合成後續商品", price="12"))

    def meal(release=RELEASE, at=AT):
        with Session(conn) as db:
            return meal_cost_from_db(db, [IngredientRequirement(FOOD, Decimal("150"))], (),
                                     Decimal("40"), now=at, recipe_release_id=release).lines[0]

    def plan(publication_id, revision):
        return env.call(publisher, "activation_plan", env.key(), "price_policy", {
            "publication_id": publication_id, "expected_binding_revision": revision, "reason": "synthetic binding",
        })

    def command(receipt, rollback=False):
        return {"target_publication_id" if rollback else "publication_id": receipt["publication_id"],
                "expected_binding_revision": receipt["expected_binding_revision"], "plan_id": receipt["plan_id"],
                "plan_hash": receipt["plan_hash"], "reason": "synthetic binding"}

    def activate(receipt, rollback=False, key=None):
        return env.call(publisher, "activate", key or env.key(), "price_policy", command(receipt, rollback), rollback)

    def member(publication_id):
        return conn.execute(sa.select(T["publication_members"]).where(
            T["publication_members"].c.publication_id == publication_id,
        )).mappings().one()

    monkeypatch.setattr(settings, "management_price_policy_enabled", False)
    first = env.content("price", correction(**identity), pins)
    first_request = env.approved(first)
    first_pub = env.call(publisher, "publish", env.key(), first_request)
    first_id = first_pub["publication_id"]
    assert not plan(first_id, 1)["can_activate"]
    assert meal().quote.price == Decimal("12")

    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    assert meal().quote.price == Decimal("12")  # published only
    receipt = plan(first_id, 1)
    assert receipt["can_activate"]
    with pytest.raises(ApiError) as forbidden:
        env.call(ACTORS[1], "activate", env.key(), "price_policy", command(receipt))
    assert forbidden.value.code == "FORBIDDEN"
    with pytest.raises(ApiError) as forged:
        env.call(publisher, "activate", env.key(), "price_policy", {**command(receipt), "plan_hash": "0" * 64})
    assert forged.value.code == "ACTIVATION_PLAN_INVALID"
    idempotency_key = env.key()
    first_binding = activate(receipt, key=idempotency_key)
    assert activate(receipt, key=idempotency_key) == first_binding
    assert first_binding["revision"] == 2
    priced = meal()
    assert (priced.purchase_price, priced.cost) == (Decimal("73.25"), Decimal("36.625"))
    assert priced.quote.price_policy_publication_id == first_id
    assert priced.quote.review_decision_id == first_request["proposal_refs"][0]["content_approval_id"]
    from app.services.budget_floor import _unit_prices
    with Session(conn) as db:
        assert _unit_prices(db, AT, {FOOD})[FOOD] == Decimal("73.25") / Decimal("300")
    assert meal("unrelated-release").missing_reason == "PRICE_POLICY_RELEASE_MISMATCH"
    assert meal(at=AT + timedelta(days=3)).missing_reason == "PRICE_EXPIRED"

    old = member(first_id)
    successor_pins = {**pins, "base_publication_id": first_id,
                      "base_publication_manifest_sha256": first_pub["manifest_hash"]}
    with pytest.raises(ApiError) as invalid:
        env.content("price", correction(**identity, provider="uniprosperity", replaces_version_id=old["version_id"]),
                    successor_pins)
    assert invalid.value.code == "PRICE_REPLACEMENT_TARGET_INVALID"
    second = env.content("price", correction(**identity, price_twd="85", replaces_version_id=old["version_id"]),
                         successor_pins)
    second_pub = env.call(publisher, "publish", env.key(), {**env.approved(second), "base_publication_id": first_id})
    second_id = second_pub["publication_id"]
    updated = member(second_id)
    assert updated["logical_id"] == old["logical_id"] and updated["version_id"] != old["version_id"]
    assert member(first_id) == old
    assert meal().purchase_price == Decimal("73.25")
    assert activate(plan(second_id, 2))["revision"] == 3
    assert meal().purchase_price == Decimal("85")
    with pytest.raises(ApiError) as stale:
        activate(receipt)
    assert stale.value.code == "BINDING_CHANGED"
    assert activate(plan(first_id, 3), rollback=True)["revision"] == 4
    assert meal().purchase_price == Decimal("73.25")
    assert bytes(conn.scalar(snapshot, {"id": plan_id})) == before


def test_pg_t13_locked_binding_reads_exact_sku_instead_of_latest_other_sku(price_env, monkeypatch):
    env, pins, identity = price_env
    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    save_fixture_observation(env, "exact-sku", quote(price="60", queried_at=OBSERVED))
    save_fixture_observation(env, "other-sku", quote(product_id="other", product_name="合成別的商品", price="12"))
    proposal = env.content("price", binding(**identity), pins)
    publication = env.call(ACTORS[2], "publish", env.key(), env.approved(proposal))
    plan = env.call(ACTORS[2], "activation_plan", env.key(), "price_policy", {
        "publication_id": publication["publication_id"], "expected_binding_revision": 1, "reason": "fixture plan",
    })
    assert plan["can_activate"]
    env.call(ACTORS[2], "activate", env.key(), "price_policy", {
        "publication_id": publication["publication_id"], "expected_binding_revision": 1,
        "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "reason": "fixture activate",
    })
    with Session(env.engine) as db:
        line = meal_cost_from_db(db, [IngredientRequirement(FOOD, Decimal("150"))], (),
                                 Decimal("40"), now=AT).lines[0]
    assert line.quote.product_id == SKU and line.quote.price == Decimal("60")
    assert line.quote.queried_at == OBSERVED
    assert line.quote.price_policy_publication_id == publication["publication_id"]


def test_pg_activation_rechecks_price_expiry_after_plan(price_env, monkeypatch):
    from app.management import service as management_service

    env, pins, identity = price_env
    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    proposal = env.content("price", correction(**identity, valid_until=utc_stamp(AT + timedelta(minutes=1))), pins)
    publication = env.call(ACTORS[2], "publish", env.key(), env.approved(proposal))
    request = {"publication_id": publication["publication_id"], "expected_binding_revision": 1, "reason": "fixture"}
    plan = env.call(ACTORS[2], "activation_plan", env.key(), "price_policy", request)
    assert plan["can_activate"]
    monkeypatch.setattr(management_service, "now", lambda: AT + timedelta(minutes=2))
    expired = env.call(ACTORS[2], "activation_plan", env.key(), "price_policy", request)
    assert not expired["can_activate"] and expired["issues"][0]["code"] == "PRICE_EXPIRED"
    with pytest.raises(ApiError) as error:
        env.call(ACTORS[2], "activate", env.key(), "price_policy", {
            **request, "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"],
        })
    assert error.value.code == "PRICE_EXPIRED"


def test_pg_successor_adds_new_food_and_keeps_inherited_review_receipts(price_env, monkeypatch):
    env, pins, identity = price_env
    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    first = env.content("price", correction(**identity), pins)
    original = env.call(ACTORS[2], "publish", env.key(), env.approved(first))

    def activate(publication, revision):
        request = {"publication_id": publication["publication_id"], "expected_binding_revision": revision, "reason": "fixture"}
        plan = env.call(ACTORS[2], "activation_plan", env.key(), "price_policy", request)
        assert plan["can_activate"], plan
        env.call(ACTORS[2], "activate", env.key(), "price_policy",
                 {**request, "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]})

    activate(original, 1)
    second_member = env.engine.execute(sa.select(T["publication_members"]).where(
        T["publication_members"].c.publication_id == identity["ingredient_publication_id"],
        T["publication_members"].c.logical_id == "FOOD_SECOND_FIXTURE",
    )).mappings().one()
    second = env.content("price", correction(
        **{**identity, "ingredient_version_id": second_member["version_id"]},
        food_id="FOOD_SECOND_FIXTURE", price_twd="31", product_id="SECOND-SKU", product_name="合成第二商品",
    ), {**pins, "base_publication_id": original["publication_id"],
        "base_publication_manifest_sha256": original["manifest_hash"]})
    successor = env.call(ACTORS[2], "publish", env.key(),
                         {**env.approved(second), "base_publication_id": original["publication_id"]})
    assert successor["member_count"] == 2
    activate(successor, 2)
    with Session(env.engine) as db:
        cost = meal_cost_from_db(db, [IngredientRequirement(food, Decimal("150"))
                                     for food in (FOOD, "FOOD_SECOND_FIXTURE")], (), Decimal("100"), now=AT)
    assert [line.purchase_price for line in cost.lines] == [Decimal("73.25"), Decimal("31")]
    assert all(line.quote.price_policy_publication_id == successor["publication_id"] for line in cost.lines)


def test_pg_current_future_windows_and_unrelated_expired_food_are_explicit(price_env, monkeypatch):
    from app.management import service as management_service

    env, pins, identity = price_env
    monkeypatch.setattr(settings, "management_price_policy_enabled", True)
    other_version = env.engine.scalar(sa.select(T["publication_members"].c.version_id).where(
        T["publication_members"].c.publication_id == identity["ingredient_publication_id"],
        T["publication_members"].c.logical_id == "FOOD_SECOND_FIXTURE",
    ))
    current = env.content("price", correction(**identity, valid_until=utc_stamp(AT + timedelta(minutes=10))), pins)
    soon_expired = env.content("price", correction(
        **{**identity, "ingredient_version_id": other_version}, food_id="FOOD_SECOND_FIXTURE",
        product_id="SECOND-SKU", product_name="合成第二商品", valid_until=utc_stamp(AT + timedelta(minutes=1)),
    ), pins)
    request = env.approved(current)
    request["proposal_refs"].extend(env.approved(soon_expired)["proposal_refs"])
    first = env.call(ACTORS[2], "publish", env.key(), request)

    def activate(publication, revision):
        request = {"publication_id": publication["publication_id"], "expected_binding_revision": revision, "reason": "fixture"}
        plan = env.call(ACTORS[2], "activation_plan", env.key(), "price_policy", request)
        assert plan["can_activate"], plan
        env.call(ACTORS[2], "activate", env.key(), "price_policy",
                 {**request, "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]})
        return plan

    activate(first, 1)
    future = env.content("price", correction(**identity, price_twd="89",
        valid_from=utc_stamp(AT + timedelta(minutes=10)), valid_until=utc_stamp(AT + timedelta(minutes=20))),
        {**pins, "base_publication_id": first["publication_id"], "base_publication_manifest_sha256": first["manifest_hash"]})
    successor = env.call(ACTORS[2], "publish", env.key(),
                         {**env.approved(future), "base_publication_id": first["publication_id"]})
    monkeypatch.setattr(management_service, "now", lambda: AT + timedelta(minutes=2))
    plan = activate(successor, 2)
    validate("ActivationPlan", plan)
    assert any(issue["code"] == "PRICE_EXPIRED" and issue["severity"] == "warning" for issue in plan["issues"])
    for minute, expected in [(2, "73.25"), (12, "89")]:
        with Session(env.engine) as db:
            cost = meal_cost_from_db(db, [IngredientRequirement(food, Decimal("150"))
                                         for food in (FOOD, "FOOD_SECOND_FIXTURE")], (), Decimal("100"),
                                    now=AT + timedelta(minutes=minute))
        assert cost.lines[0].purchase_price == Decimal(expected)
        assert cost.lines[1].missing_reason == "PRICE_EXPIRED" and cost.lines[1].cost is None
