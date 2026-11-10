from sqlalchemy import func, select

from app.api import management_recipes
from app.management.models import tables
from app.services.recipe_step_style import check_recipe_steps
from app.source_adapters.icook import parse_recipe
from test_icook_recipe import recipe_html, URL
from test_management_api import client
from test_management_foundation import env
from test_management_generated_recipes import intake, PATH
from test_management_recipe_candidates import candidate, request


def test_every_candidate_preview_uses_the_same_checker_without_saving(intake, env, monkeypatch):
    http, _, _, result_id, _, _ = intake
    steps = ["步驟一：將雞蛋放進鍋中，煎 2 分鐘。", "將雞蛋倒入Ａ鍋。"]
    expected = check_recipe_steps(enumerate(steps, 1))
    fixture = candidate()
    fixture["recipe"]["steps"] = steps
    body = {"fixture_json": request(fixture)["fixture_json"]}
    uploaded = http.post("/admin/v1/recipe-candidates/fixtures/preview", json=body)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["step_checks"] == expected

    snapshot = parse_recipe(URL, recipe_html())
    for row, text in zip(snapshot["tables"]["recipe_steps"], steps):
        row["instruction"] = text
    monkeypatch.setattr(management_recipes, "fetch_recipe", lambda _: snapshot)
    icook = http.post("/admin/v1/recipe-candidates/icook/preview", json={"url": URL})
    assert icook.status_code == 200, icook.text
    assert icook.json()["step_checks"] == expected
    assert [row["instruction"] for row in icook.json()["tables"]["recipe_steps"]] == steps

    generated = http.post(PATH + "/preview", json={"result_id": str(result_id), "dish_index": 1})
    assert generated.status_code == 200, generated.text
    preview = generated.json()
    assert preview["step_checks"] == check_recipe_steps(enumerate(preview["snapshot"]["steps"], 1))
    assert "step_checks" not in preview["snapshot"]
    with env.engine.connect() as db:
        for name in ("artifacts", "proposals", "publications", "tasks"):
            assert db.scalar(select(func.count()).select_from(tables[name])) == 0
