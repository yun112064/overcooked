"""Opt-in test policies for features that require locally reviewed source data."""
import json

import pytest


def write_policy(tmp_path, filename, data):
    path = tmp_path / filename
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def staple_policy(tmp_path, monkeypatch):
    from app.services import staples

    samples = {
        "白飯": {"food_id": "FOOD000296", "tfda_name": "白飯", "tfda_sample_id": "A0550601",
                "aliases": ["白米", "米飯"], "default_grams_per_n": "160", "weight_basis": "cooked",
                "source_cooking_method": "電鍋煮", "adjustable_min": "100", "adjustable_max": "250"},
        "糙米飯": {"food_id": "TEST-BROWN-RICE", "tfda_name": "糙稉米平均值", "aliases": ["糙米"],
                 "default_grams_per_n": "75", "weight_basis": "raw", "consumed_as_is": True,
                 "adjustable_min": "50", "adjustable_max": "125", "display_name": "糙米（生）",
                 "cooked_grams_per_n": "150"},
    }
    for label, name, grams in (
        ("五穀飯", "五穀米", "75"), ("胚芽米飯", "胚芽稉米平均值", "75"),
        ("麵", "乾麵條", "80"), ("麵線", "麵線", "80"), ("烏龍麵", "冷凍烏龍麵", "210"),
        ("麵包", "土司", "60"), ("地瓜", "黃肉甘藷", "150"), ("玉米", "甜玉米", "250"),
    ):
        samples[label] = {"food_id": f"TEST-{label}", "tfda_name": name,
                          "default_grams_per_n": grams, "weight_basis": "raw", "consumed_as_is": True}
    policy = {**staples.table(), "version": "synthetic-test-policy", "options": samples}
    monkeypatch.setattr(staples, "STAPLES_PATH", write_policy(tmp_path, "staples.json", policy))
    staples.table.cache_clear()
    yield
    staples.table.cache_clear()


@pytest.fixture
def category_zero_policy(tmp_path, monkeypatch):
    from app.services import nutrient_category_zero

    rules = [
        {"id": "test-fiber", "prefixes": ["I", "J04", "K"], "nutrients": ["dietary_fiber_g"]},
        {"id": "test-oil", "prefixes": ["M11", "P0900201"],
         "nutrients": ["carbohydrate_g", "sugars_g", "dietary_fiber_g", "sodium_mg"]},
        {"id": "test-sugar-salt", "prefixes": ["N0100101", "N0100201", "P03"],
         "nutrients": ["protein_g", "fat_g", "dietary_fiber_g"]},
    ]
    for rule in rules:
        rule["reason"] = "Synthetic rule for testing precedence and traceability."
    path = write_policy(tmp_path, "zero-rules.json", {"rules": rules})
    monkeypatch.setattr(nutrient_category_zero, "RULES_PATH", path)
    nutrient_category_zero.rules.cache_clear()
    yield
    nutrient_category_zero.rules.cache_clear()


@pytest.fixture
def embedding_test_price(tmp_path, monkeypatch):
    from app.services import spend

    pricing = spend.pricing()
    models = {**pricing["models"], "text-embedding-3-small": {"input": "0.02", "output": "0"}}
    path = write_policy(tmp_path, "pricing.json", {**pricing, "models": models})
    monkeypatch.setattr(spend.settings, "model_pricing_path", path)
