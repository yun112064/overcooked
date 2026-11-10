import hashlib
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import management_recipes
from app.api.deps import current_auth
from app.management.models import tables
from app.management.service import Management
from app.source_adapters.icook import ICookError, fetch_recipe, parse_recipe
from test_management_api import client
from test_management_foundation import ACTORS, env


URL = "https://icook.tw/recipes/123"


def recipe_html(url=URL):
    ingredients = [
        ("花生粉", "0.5公斤"), ("芝麻", "適量"), ("牛奶", "1/2杯"),
        ("糖", "1–2克"), ("鹽", "1/3匙"), ("水", "120毫升"),
    ]
    structured = {"@graph": [
        {"@type": ["Recipe"], "recipeYield": "1 批", "keywords": ["測試", "示範"],
         "recipeIngredient": [f"{name} {amount}" for name, amount in ingredients],
         "recipeInstructions": [{"@type": "HowToSection", "itemListElement": [
             {"@type": "HowToStep", "text": "加入材料。"},
             {"@type": "HowToStep", "text": "攪拌。\n  裝盤。"},
         ]}]},
        [{"@type": "BreadcrumbList"}],
    ]}
    lines = "".join(
        f'<li><div class="ingredient-name"><a>{name}</a></div>'
        f'<div class="ingredient-unit">{amount}</div></li>'
        for name, amount in ingredients
    )
    return (
        f'<link rel="canonical" href="{url}"><h1 id="recipe-name">測試食譜</h1>'
        f'<ul>{lines}</ul><script type="application/ld+json">{json.dumps(structured)}</script>'
    ).encode()


def test_structure_and_unknown_values():
    page = recipe_html()
    result = parse_recipe(URL, page)
    assert result["source"]["html_sha256"] == hashlib.sha256(page).hexdigest()
    assert result["source"]["rights_status"] == "pending_review"
    tables = result["tables"]
    assert tables["source_archive"][0]["servings_raw"] == "1 批"
    assert tables["source_archive"][0]["tags_raw"] == "測試；示範"
    assert "攪拌。\n  裝盤。" in tables["source_archive"][0]["steps_raw"]
    assert tables["recipe_steps"][1]["instruction"] == "攪拌。 裝盤。"
    assert tables["recipes"][0]["category"] is None
    rows = tables["recipe_ingredients"]
    assert [(r["amount"], r["unit"], r["weight_g"]) for r in rows] == [
        ("0.5", "kg", "500"), (None, None, None), ("0.5", "杯", None),
        (None, None, None), (None, "匙", None), ("120", "ml", None),
    ]
    assert all(r["Food_ID"] is None and r["protein_g"] is None for r in rows)
    assert all(r["recipe_id"] == tables["recipes"][0]["recipe_id"] for r in rows)
    assert tables["ingredient_aliases"] == tables["allergens"] == tables["標準食材整合"] == []


def test_changed_markup_keeps_raw_without_guessing():
    page = recipe_html().replace(b'ingredient-name', b'changed')
    rows = parse_recipe(URL, page)["tables"]["recipe_ingredients"]
    assert rows[0]["raw_text"] == "花生粉 0.5公斤"
    assert rows[0]["canonical_name"] is rows[0]["weight_g"] is None
    with pytest.raises(ICookError, match="缺少可核對"):
        parse_recipe(URL, recipe_html(url="https://icook.tw/recipes/456"))


@pytest.mark.parametrize("url", [
    "http://icook.tw/recipes/123", "https://icook.tw.evil.test/recipes/123",
    "https://icook.tw@127.0.0.1/recipes/123", URL + "?next=other", URL + "/print",
])
def test_other_urls_never_request_network(url):
    transport = httpx.MockTransport(lambda _: pytest.fail("unexpected network call"))
    with pytest.raises(ICookError) as error:
        fetch_recipe(url, transport=transport)
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("rules, response_status, expected", [
    ("Disallow: /recipes/*.json", 200, None),
    ("Disallow: /recipes/*$\nAllow: /recipes/123$", 200, None),
    ("Disallow: /recipes/*$", 200, "ICOOK_ROBOTS_BLOCKED"),
    ("Disallow: /private", 302, "ICOOK_FETCH_FAILED"),
    ("Disallow: /private", 404, "ICOOK_NOT_FOUND"),
])
def test_fetch_honors_robots_and_does_not_follow_redirects(rules, response_status, expected):
    urls = []

    def respond(request):
        urls.append(str(request.url))
        assert request.headers["user-agent"] == "OvercookedRecipePreview/1.0"
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"Content-Type": "text/plain"},
                                  text=f"User-agent: *\n{rules}")
        assert str(request.url) == URL
        return httpx.Response(response_status, content=recipe_html(), headers={
            "Content-Type": "text/html; charset=utf-8", "Location": "https://example.invalid",
        })

    transport = httpx.MockTransport(respond)
    if expected:
        with pytest.raises(ICookError) as error:
            fetch_recipe(URL, transport=transport)
        assert error.value.code == expected
    else:
        assert fetch_recipe(URL + "/", transport=transport)["source"]["observed_at"]
    assert urls == (
        ["https://icook.tw/robots.txt"] if expected == "ICOOK_ROBOTS_BLOCKED"
        else ["https://icook.tw/robots.txt", URL]
    )


def test_admin_preview_checks_permissions_before_fetch_and_does_not_write(client, env, monkeypatch):
    http, app = client
    app.include_router(management_recipes.router)
    calls = []

    def preview(url):
        calls.append(url)
        return parse_recipe(url, recipe_html())

    monkeypatch.setattr(management_recipes, "fetch_recipe", preview)
    path = "/admin/v1/recipe-candidates/icook/preview"
    assert http.post(path, json={"url": URL}).status_code == 403
    assert calls == []
    env.grants(ACTORS[1], ["recipe_editor"], ["recipe"])
    assert http.post(path, json={"url": 123}).status_code == 422
    assert http.post(path, json={"url": URL}, headers={"Origin": "https://example.invalid"}).status_code == 403
    assert calls == []
    result = http.post(path, json={"url": URL})
    assert result.status_code == 200
    assert result.json()["status"] == "pending_review"
    assert calls == [URL]
    with env.engine.connect() as connection:
        for name in ("artifacts", "proposals", "publications"):
            assert connection.scalar(select(func.count()).select_from(tables[name])) == 0


def test_save_preview_is_bound_to_actor_and_idempotent(client, env, monkeypatch):
    http, app = client
    app.include_router(management_recipes.router)
    env.grants(ACTORS[1], ["recipe_editor"], ["recipe"])
    env.grants(ACTORS[2], ["recipe_editor"], ["recipe"])
    calls = []

    def fetch(url):
        calls.append(url)
        return parse_recipe(url, recipe_html())

    monkeypatch.setattr(management_recipes, "fetch_recipe", fetch)
    path = "/admin/v1/recipe-candidates/icook"
    preview = http.post(path + "/preview", json={"url": URL}).json()
    assert preview["servings_count"] is None
    token = preview["preview_id"]

    def save(value=token, key="import-once"):
        return http.post(path + "/drafts", json={"preview_id": value}, headers={"Idempotency-Key": key})

    assert save(token + "tampered").status_code == 422
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[2]))
    assert save().status_code == 422
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[1]))
    saved = save()
    assert saved.status_code == 201, saved.text
    draft = saved.json()["proposal"]
    assert draft["status"] == "draft"
    assert draft["content"]["servings"]["count"] is None
    assert draft["content"]["servings"]["basis"] == "portions"
    assert draft["content"]["servings"]["raw_text"] == "1 批"
    assert draft["content"]["category"] is None
    assert draft["content"]["ingredients"][1]["amount"]["value"] is None
    assert draft["content"]["ingredients"][-1]["unit"] == "ml"
    assert draft["content"]["ingredients"][-1]["weight_g"]["value"] is None
    assert save().headers["Idempotency-Replayed"] == "true"
    assert save(key="other-click").json()["existing"] is True
    assert calls == [URL]
    assert http.post(path + "/preview", json={"url": URL}).json()["existing_proposal_id"] == draft["proposal_id"]
    with env.engine.connect() as connection:
        for name, count in (("artifacts", 1), ("proposals", 1), ("publications", 0), ("review_decisions", 0)):
            assert connection.scalar(select(func.count()).select_from(tables[name])) == count


def test_icook_measurements_must_be_completed_and_confirmed_before_review(client, env, monkeypatch):
    http, app = client
    app.include_router(management_recipes.router)
    env.grants(ACTORS[1], ["recipe_editor"], ["recipe"])
    env.grants(ACTORS[2], ["recipe_reviewer"], ["recipe"])
    monkeypatch.setattr(management_recipes, "fetch_recipe", lambda url: parse_recipe(url, recipe_html()))
    path = "/admin/v1/recipe-candidates/icook"
    token = http.post(path + "/preview", json={"url": URL}).json()["preview_id"]
    saved = http.post(path + "/drafts", json={"preview_id": token}, headers={"Idempotency-Key": "import"})
    assert saved.status_code == 201, saved.text
    p = saved.json()["proposal"]
    pid = p["proposal_id"]
    command = {"expected_revision": 1, "expected_content_hash": p["content_hash"], "reason": "synthetic review"}
    confirm = path + f"/{pid}/confirm-quantities"
    proposal_url = "/admin/v1/proposals/" + pid
    assert http.post(confirm, json=command, headers={"Idempotency-Key": "confirm"}).status_code == 403
    source = http.get(path + f"/{pid}/source").json()
    assert source["quantity_issues"] == ["ICOOK_PORTIONS_REQUIRED", "ICOOK_QUANTITY_REQUIRED"]
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[2]))
    assert http.post(confirm, json=command, headers={"Idempotency-Key": "confirm"}).status_code == 409
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[1]))

    def revise(key, command, content):
        return http.post(proposal_url + "/revisions", headers={"Idempotency-Key": key}, json={
            **command, "domain": "recipe", "dependency_pins": p["dependency_pins"], "content": content,
        })

    content = p["content"]
    content["servings"]["basis"] = "batch"
    assert revise("batch", command, content).status_code == 422
    content["servings"].update(basis="portions", count="4")
    for line in content["ingredients"]:
        if line["amount"]["value"] is None:
            line.update(amount={"value": "2.5", "status": "declared", "evidence_ids": [], "unknown_reason": None}, unit="ml")
    revised = revise("complete", command, content)
    assert revised.status_code == 201, revised.text
    p = revised.json()
    command.update(expected_revision=p["revision"], expected_content_hash=p["content_hash"])

    def issues():
        with Session(env.engine) as db:
            s = Management(db, ACTORS[1])
            row = s.proposal(pid)
            return {issue["code"] for issue in s.issues(row, s.revision(row))}

    assert "ICOOK_REVIEWER_CONFIRMATION_REQUIRED" in issues()
    assert "SOURCE_WEIGHT_REQUIRED" not in issues()
    assert http.post(proposal_url + "/submit", json=command, headers={"Idempotency-Key": "blocked"}).status_code == 409
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[2]))
    assert http.post(confirm, json=command, headers={"Idempotency-Key": "confirm2"}).status_code == 200
    assert "ICOOK_REVIEWER_CONFIRMATION_REQUIRED" not in issues()
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(user_id=uuid.UUID(ACTORS[1]))
    content["servings"]["count"] = "5"
    next_revision = revise("update", command, content)
    assert next_revision.status_code == 201, next_revision.text
    assert "ICOOK_REVIEWER_CONFIRMATION_REQUIRED" in issues()
    assert next_revision.json()["status"] == "draft"
