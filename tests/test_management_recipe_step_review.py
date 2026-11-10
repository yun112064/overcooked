"""Check review blockers for iCook and generated drafts on disposable SQLite."""

import copy
import json
from types import SimpleNamespace
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.api import management_dashboard, management_recipes
from app.api.deps import current_auth
from app.db.models import GenerationResult
from app.management.models import tables
from app.management.service import legacy_capture
from app.models.recipe import IngredientAlias
from app.source_adapters.icook import parse_recipe
from test_management_api import client
from test_management_foundation import ACTORS, env
from test_management_generated_recipes import create_result_tables, seed_generated_result
from test_management_recipe_candidates import recipe_release


@pytest.mark.parametrize("source", ["icook", "generated"])
def test_draft_step_blockers_clear_after_revision(source, client, env, recipe_release, monkeypatch):
    http, app = client
    app.include_router(management_recipes.router)
    app.include_router(management_dashboard.router)
    env.grants(ACTORS[1], ["recipe_editor"], ["recipe"])
    _, pins = recipe_release
    legacy_release = pins["legacy_recipe_release_id"]
    actor = uuid.UUID(ACTORS[1])
    candidate = "/admin/v1/recipe-candidates/" + source
    steps = ["Ａ鍋加水。", "步驟二：然後裝盤。", "把材料放入原鍋。"]

    def post(url, body):
        response = http.post(url, json=body, headers={"Idempotency-Key": env.key()})
        assert response.is_success, response.text
        return response.json()

    if source == "icook":
        url = "https://icook.tw/recipes/33570"
        recipe = {"@type": "Recipe", "name": "隔離測試魚肉", "recipeYield": "2 人份",
                  "recipeIngredient": ["魚肉 50 g"],
                  "recipeInstructions": [{"@type": "HowToStep", "text": step} for step in steps]}
        html = (
            f'<link rel="canonical" href="{url}"><h1 id="recipe-name">隔離測試魚肉</h1>'
            '<div class="ingredient-name">魚肉</div><div class="ingredient-unit">50 g</div>'
            f'<script type="application/ld+json">{json.dumps(recipe, ensure_ascii=False)}</script>'
        )
        monkeypatch.setattr(management_recipes, "fetch_recipe", lambda url: parse_recipe(url, html.encode()))
        preview = post(candidate + "/preview", {"url": url})
        intake = {"preview_id": preview["preview_id"]}
    else:
        create_result_tables(env.engine)
        with Session(env.engine) as db, db.begin():
            db.add_all(IngredientAlias(food_id=food_id, recipe_release_id=legacy_release, canonical_name=name)
                       for food_id, name in [("FOOD000001", "番茄"), ("FOOD000002", "雞蛋")])
            household, result_id = seed_generated_result(db, actor, steps=steps)
        with Session(env.engine) as db:
            pins = {**pins, "legacy_db_capture_sha256": legacy_capture(db)}
            original_result = copy.deepcopy(db.get(GenerationResult, result_id).payload)
        app.dependency_overrides[current_auth] = lambda: SimpleNamespace(
            user_id=actor, session=SimpleNamespace(household_id=household),
        )
        selection = {"result_id": str(result_id), "dish_index": 1}
        preview = post(candidate + "/preview", selection)
        intake = {**selection, "snapshot_sha256": preview["snapshot_sha256"]}

    assert [f["status"] for f in preview["step_checks"]["findings"]] == ["needs_review", "suggested", "needs_review"]
    draft = post(candidate + "/drafts", intake)["proposal"]
    assert draft["status"] == "draft"
    assert [step["instruction"] for step in draft["content"]["steps"]] == steps
    base = "/admin/v1/proposals/" + draft["proposal_id"]
    source_id = draft["content"]["sources"][0]["artifact_id"]
    with Session(env.engine) as db:
        original_source = db.scalar(sa.select(tables["artifacts"].c.payload).where(tables["artifacts"].c.id == source_id))

    def workflow():
        response = http.get(base + "/workflow")
        assert response.status_code == 200, response.text
        return response.json()

    def revise(proposal, content):
        result = post(base + "/revisions", {**env.expected(proposal), "domain": "recipe",
                                             "dependency_pins": pins, "content": content})
        assert result["status"] == "draft"
        return result

    def validate(proposal):
        task = post(base + "/validations", env.expected(proposal))
        env.call(ACTORS[1], "run_validation", task["task_id"])
        return workflow()["validation"]

    blockers = workflow()["step_style_issues"]
    assert [(i["code"], i["json_pointer"]) for i in blockers] == [
        ("RECIPE_STEP_STYLE_REVIEW_REQUIRED", "/steps/0/instruction"),
        ("RECIPE_STEP_STYLE_REVIEW_REQUIRED", "/steps/2/instruction"),
    ]
    assert blockers[0]["message"].startswith("第 1 步") and "鍋具代號" in blockers[0]["message"]
    assert blockers[1]["message"].startswith("第 3 步") and "前文" in blockers[1]["message"]
    assert all(i["severity"] == "blocking" for i in blockers)

    content = copy.deepcopy(draft["content"])
    content.update(category="主菜", cuisine="台式", meal_type="午餐")
    if source == "icook":
        content["ingredients"][0]["food_id"] = "SYNTHETIC_FOOD"
    else:
        content["servings"]["count"] = "2"
        proof = env.call(ACTORS[1], "upload", env.key(), "recipe", "manual_entry", "text/plain",
                         b"synthetic recipe weight evidence", "Fixture evidence")
        content["sources"].append(dict(artifact_id=proof["artifact_id"], artifact_sha256=proof["sha256"],
                                       origin_kind="manual_entry", source_policy_version_id=None))
        for line in content["ingredients"]:
            measurement = dict(status="declared", value="50", evidence_ids=[proof["artifact_id"]], unknown_reason=None)
            line.update(amount=measurement, weight_g=measurement, unit="g")
    pending = revise(draft, content)
    assert workflow()["validation"] is None
    denied = http.post(base + "/submit", json=env.expected(pending), headers={"Idempotency-Key": env.key()})
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "VALIDATION_REQUIRED"
    assert denied.json()["error"]["details"]["issues"] == blockers
    validation = validate(pending)
    assert validation["issues"] == blockers and not validation["catalog_ready"]
    assert http.get(base).json()["status"] == "draft"

    content["steps"][0]["instruction"] = "用煮湯的鍋加水。"
    content["steps"][2]["instruction"] = "把材料放入煮湯的鍋。"
    corrected = revise(pending, content)
    assert workflow()["validation"] is None and workflow()["step_style_issues"] == []
    assert corrected["content"]["steps"][1]["instruction"] == steps[1]
    validation = validate(corrected)
    assert validation["catalog_ready"] and validation["issues"] == []
    assert http.get(base).json()["status"] == "draft"

    with Session(env.engine) as db:
        assert legacy_capture(db) == pins["legacy_db_capture_sha256"]
        assert db.scalar(sa.select(tables["artifacts"].c.payload).where(tables["artifacts"].c.id == source_id)) == original_source
        if source == "generated":
            assert db.get(GenerationResult, result_id).payload == original_result
        for name in ("review_decisions", "publications"):
            assert db.scalar(sa.select(sa.func.count()).select_from(tables[name])) == 0
        assert db.scalar(sa.select(sa.func.count()).select_from(tables["commands"]).where(
            tables["commands"].c.operation == "proposal.submit")) == 0
