"""The approved T11 candidate snapshot must remain unchanged before publication."""
from pathlib import Path

import pytest

from knowledge_rag import apply_reviewed


def test_reviewed_knowledge_matches_all_123_publication_records():
    candidates, decisions = apply_reviewed.checked_records()
    assert len(candidates) == len(decisions) == 123
    assert candidates[0]["excerpt"].endswith(decisions[0]["fact_zh_reviewed"])


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


def test_every_reviewed_record_has_recorded_source_evidence():
    _, decisions = apply_reviewed.checked_records()
    notes = [__import__("json").loads(apply_reviewed.review_note(d)) for d in decisions]
    assert sum("source_snapshot_sha256" in n for n in notes) == 113
    blocked = [n for n in notes if "source_snapshot_sha256" not in n]
    assert len(blocked) == 10 and all(n["http_fetch_status"] == 403 and n["access_note"] for n in blocked)


def test_record_without_any_source_evidence_is_rejected():
    _, decisions = apply_reviewed.checked_records()
    bare = {k: v for k, v in decisions[0].items()
            if k not in ("source_snapshot_sha256", "http_fetch_status", "access_note")}
    with pytest.raises(ValueError, match="No source evidence"):
        apply_reviewed.review_note(bare)
