"""The approved T11 candidate snapshot must remain unchanged before publication."""
from hashlib import sha256

import pytest

from knowledge_rag import apply_reviewed


@pytest.mark.skipif(not (apply_reviewed.CANDIDATES.is_file() and apply_reviewed.REVIEWS.is_file()),
                    reason="Local reviewed knowledge snapshot is not distributed with code")
def test_reviewed_knowledge_matches_all_123_publication_records():
    candidates, decisions = apply_reviewed.checked_records()
    assert len(candidates) == len(decisions) == 123
    assert candidates[0]["excerpt"].endswith(decisions[0]["fact_zh_reviewed"])
    assert all(decision["formal_index_approval_granted"] is False for decision in decisions)
    assert sum(bool(decision.get("source_snapshot_sha256")) for decision in decisions) == 113


def test_changed_excerpt_cannot_reuse_existing_approval(tmp_path, monkeypatch):
    changed = tmp_path / "candidates.json"
    original = '{"records":[{"excerpt":"Synthetic reviewed text"}]}'
    changed.write_text(original.replace("reviewed", "modified"), encoding="utf-8")
    monkeypatch.setattr(apply_reviewed, "CANDIDATES", changed)
    monkeypatch.setattr(apply_reviewed, "EXPECTED_HASHES", {
        changed: sha256(original.encode()).hexdigest(),
    })
    with pytest.raises(ValueError, match="Reviewed file changed"):
        apply_reviewed.checked_records()


def test_dish_name_normalization_matches_tomato_spellings():
    from app.services.recipe_import import normalize_name

    assert normalize_name("番茄炒蛋") == normalize_name("蕃茄炒蛋")
