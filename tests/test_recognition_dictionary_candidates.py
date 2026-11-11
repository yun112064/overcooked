"""Synthetic SQLite verifies candidate selection; no real model or DB writes."""
import json
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.recipe import IngredientAlias
from app.services import ingredients, recognition


@pytest.mark.parametrize('ensure_ascii', [True, False])
def test_ambiguous_alias_lists_all_candidates_without_assigning_detection(ensure_ascii):
    engine = create_engine('sqlite://', json_serializer=lambda v: json.dumps(v, ensure_ascii=ensure_ascii))
    IngredientAlias.__table__.create(engine)
    with Session(engine) as db:
        db.add_all([
            IngredientAlias(food_id='FOOD001016', canonical_name='番茄',
                            aliases=[], ambiguous_aliases=['牛番茄']),
            IngredientAlias(food_id='FOOD001444', canonical_name='蕃茄',
                            aliases=[], ambiguous_aliases=['牛番茄']),
            IngredientAlias(food_id='FOOD000867', canonical_name='高麗菜',
                            aliases=['甘藍'], ambiguous_aliases=[]),
        ])
        db.flush()
        assert {item['ingredient_id'] for item in ingredients.search(db, '牛番茄')} == {
            'FOOD001016', 'FOOD001444'}
        assert ingredients.search(db, '甘藍') == [
            {'ingredient_id': 'FOOD000867', 'name': '高麗菜'}]
        assert len(ingredients.search(db, '牛番茄', limit=1)) == 1
        assert ingredients.unknown_ids(db, ['FOOD999999']) == ['FOOD999999']
        assert recognition.suggestions([{'label': 'Beef-tomato', 'confidence': 0.95}]) == [
            {'name': '牛番茄', 'ingredient_id': None}]
        assert all(row.ambiguous_aliases == ['牛番茄'] for row in db.query(IngredientAlias)
                   .filter(IngredientAlias.food_id.in_(['FOOD001016', 'FOOD001444'])))
