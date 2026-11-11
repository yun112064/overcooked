from decimal import Decimal
from types import SimpleNamespace

from app.services import staples, meal_request


def test_auto_is_a_choice_not_no_staple_and_filters_every_exclusion(monkeypatch):
    config = {'version':'synthetic-test','review_status':'user_confirmed','none_values':['不需要主食'],
              'options':{
                  '白飯':{'food_id':'RICE','tfda_name':'白飯','default_grams_per_n':'150'},
                  '糙米飯':{'food_id':'BROWN','tfda_name':'糙米飯','default_grams_per_n':'150'},
                  '待審主食':{'food_id':'UNKNOWN','tfda_name':None}}}
    monkeypatch.setattr(staples, 'table', lambda:config)
    assert not staples.is_none_value('不指定')
    assert meal_request.staple_issue('不指定') is None
    assert [s.line.food_id for s in staples.automatic_options({'RICE'})] == ['BROWN']
    assert staples.automatic_options({'RICE','BROWN'}) == []
    assert sum(o['label']=='不指定' for o in meal_request.staple_options()['options']) == 1


def test_auto_preview_checks_nutrition_and_selects_passing_choice(monkeypatch):
    from app.services import t12_planning as planning
    # Exercise the real dispatcher recursively with two synthetic nutrition outcomes.
    calls=[]
    original=planning.preview
    monkeypatch.setattr(meal_request, 'restrictions', lambda *a:{'excluded_food_ids':['FORBIDDEN']})
    def options(excluded):
        assert excluded == ['FORBIDDEN']
        return [SimpleNamespace(label='白飯'),SimpleNamespace(label='糙米飯')]
    monkeypatch.setattr(staples,'automatic_options',options)
    def preview(db, *, draft, members, recipe_ids):
        if staples.is_auto_value(draft.staple):
            return original(db,draft=draft,members=members,recipe_ids=recipe_ids)
        calls.append(draft.staple)
        return {'staple':draft.staple,'nutrition_passed':draft.staple=='糙米飯',
                'adoption_blocked':draft.staple!='糙米飯'}
    monkeypatch.setattr(planning,'preview',preview)
    result=preview(None,draft=meal_request.MealRequestDraft(staple='不指定'),members={},recipe_ids=['REAL_ID_ONLY_AT_RUNTIME'])
    assert calls==['白飯','糙米飯']
    assert result['staple']=='糙米飯' and result['nutrition_passed']
