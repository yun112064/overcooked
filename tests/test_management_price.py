"""T02 management gates with synthetic observations and an isolated PostgreSQL workflow."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.errors import ApiError, register_error_handlers
from app.api.management_price import router, service
from app.db.models import User
from app.management.contracts import rules_pin, validate
from app.management.models import tables as T
from app.management.price_policy import ManagedPrice, list_observations, preview_price, utc_stamp
from app.management.service import Management, legacy_capture
from app.models.recipe import IngredientAlias, RecipeDataRelease
from app.source_adapters.price_fixture import collect_fixture
from app.source_adapters.price_import import install
from app.source_adapters.price_models import IngredientQuery, PriceQuote
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
            publication = env.call(ACTORS[2], "publish", env.key(), env.approved(ingredient))
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
        actor = ACTORS[1]
        assert client.get(path, params={**params, "as_of": "2026-02-31T00:00:00Z"}).status_code == 422
        assert client.get(path, params=params).json() == result
