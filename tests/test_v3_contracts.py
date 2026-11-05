"""Architecture integration boundaries; synthetic inputs, no source imports."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import t12_planning


def test_preview_without_selected_version_does_not_open_a_database_connection():
    db = MagicMock()
    with patch.object(t12_planning, 'settings', SimpleNamespace(generation_nutrition_import_id=None)):
        with pytest.raises(t12_planning.T12PlanningUnavailable) as error:
            t12_planning.preview(db, draft=MagicMock(), members={}, recipe_ids=['synthetic-recipe'])
    assert error.value.code == 'NUTRITION_VERSION_REQUIRED'
    db.connection.assert_not_called()


def test_preview_passes_selected_import_and_policy_to_shared_engine():
    db = MagicMock()
    draft = SimpleNamespace(staple='不需要主食')
    configuration = SimpleNamespace(
        generation_nutrition_import_id='synthetic-pinned-import',
        nutrition_rules_path=Path('synthetic-rules-not-read.json'),
        t12_preview_conversion='t04',
    )
    result = MagicMock()
    result.to_dict.return_value = {'status': 'calculation_incomplete'}
    with (patch.object(t12_planning, 'settings', configuration),
          patch.object(t12_planning.meal_request, 'target_participations', return_value=['synthetic-participation']),
          patch.object(t12_planning.household, 'target_service', return_value=MagicMock()),
          patch.object(t12_planning, 'validate_database_menu', return_value=result) as engine):
        assert t12_planning.preview(db, draft=draft, members={}, recipe_ids=['R1']) == {
            'status': 'calculation_incomplete'}
    assert engine.call_args.kwargs['nutrition_import_id'] == 'synthetic-pinned-import'
    assert engine.call_args.kwargs['conversion'] == 't04'
    assert engine.call_args.kwargs['nutrition_source'] == 'imported'


def test_preview_database_error_is_distinct_from_missing_nutrition():
    configuration = SimpleNamespace(generation_nutrition_import_id='synthetic',
                                    nutrition_rules_path=Path('unused'), t12_preview_conversion='t04')
    with (patch.object(t12_planning, 'settings', configuration),
          patch.object(t12_planning.meal_request, 'target_participations', return_value=[]),
          patch.object(t12_planning.household, 'target_service', return_value=MagicMock()),
          patch.object(t12_planning, 'validate_database_menu', side_effect=t12_planning.NutritionDataUnavailable)):
        with pytest.raises(t12_planning.T12PlanningUnavailable) as error:
            t12_planning.preview(MagicMock(), draft=SimpleNamespace(staple='不需要主食'), members={}, recipe_ids=['R1'])
    assert error.value.code == 'NUTRITION_DATA_UNAVAILABLE'


def test_nutrition_preview_requires_authentication():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.post('/meal-requests/00000000-0000-0000-0000-000000000001/nutrition-preview',
                               json={'recipe_ids': ['R1']}, headers={'Origin': 'http://localhost:8080'})
    assert response.status_code == 401
