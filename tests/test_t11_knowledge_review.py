"""The approved T11 candidate snapshot must remain unchanged before publication."""
from pathlib import Path

import pytest

from knowledge_rag import apply_reviewed


def test_reviewed_knowledge_matches_all_123_publication_records():
    candidates, decisions = apply_reviewed.checked_records()
    assert len(candidates) == len(decisions) == 123
    assert candidates[0]["excerpt"].endswith(decisions[0]["fact_zh_reviewed"])
    assert all(decision["formal_index_approval_granted"] is False for decision in decisions)
    assert sum(bool(decision.get("source_snapshot_sha256")) for decision in decisions) == 113


def test_changed_excerpt_cannot_reuse_existing_approval(tmp_path, monkeypatch):
    changed = tmp_path / "candidates.json"
    text = apply_reviewed.CANDIDATES.read_text(encoding="utf-8")
    changed.write_text(text.replace("整理摘要（非逐字原文）：", "未審核摘要：", 1), encoding="utf-8")
    monkeypatch.setattr(apply_reviewed, "CANDIDATES", changed)
    monkeypatch.setattr(apply_reviewed, "EXPECTED_HASHES", {
        changed: apply_reviewed.EXPECTED_HASHES[apply_reviewed.ROOT / "candidates/web_paper_2026-10-30.json"],
        apply_reviewed.REVIEWS: apply_reviewed.EXPECTED_HASHES[apply_reviewed.REVIEWS],
    })
    with pytest.raises(ValueError, match="Reviewed file changed"):
        apply_reviewed.checked_records()


def test_dish_name_normalization_matches_tomato_spellings():
    from app.services.recipe_import import normalize_name

    assert normalize_name("番茄炒蛋") == normalize_name("蕃茄炒蛋")
