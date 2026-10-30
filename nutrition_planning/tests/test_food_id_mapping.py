from pathlib import Path
import json

import pytest

from nutrition_model.food_id_mapping import build_food_id_mappings, load_food_id_mappings


def sample(sid, name, state='raw', aliases=()):
    return dict(ingredient_id=sid,standard_name=name,state=state,aliases=list(aliases),
                source=dict(provider='TFDA',source_id=sid,source_version='test-v1'))


def test_official_average_is_used_instead_of_specific_variety(tmp_path):
    catalog=tmp_path/'catalog.json'
    catalog.write_text(json.dumps([sample('avg','青蔥平均值'),sample('specific','青蔥')]),encoding='utf-8')
    result=build_food_id_mappings([dict(food_id='FOOD000598',names=['青蔥'])],catalog)
    record=result['records'][0]
    assert record['status']=='approved' and record['sample_id']=='avg'
    assert record['match_rule']=='unique_same_state_official_average'


def test_different_states_duplicate_averages_generic_names_and_aliases_are_not_guessed(tmp_path):
    catalog=tmp_path/'catalog.json'
    catalog.write_text(json.dumps([
        sample('raw','雞蛋平均值'),sample('cooked','雞蛋','cooked'),
        sample('a','玉米平均值'),sample('b','玉米平均值'),
        sample('soy','大豆油','unspecified',aliases=['油']),
        sample('fish','魚類','raw',aliases=['小黃瓜']),
        sample('carrot','胡蘿蔔平均值','raw',aliases=['人參'])]),encoding='utf-8')
    names=['雞蛋','玉米','油','小黃瓜','人參','不存在食材']
    result=build_food_id_mappings([dict(food_id=f'FOOD{i:06}',names=[name]) for i,name in enumerate(names)],catalog)
    assert result['counts']==dict(approved=0,pending=6)
    assert [r['reason'] for r in result['records']]==[
        'sample_state_ambiguous','sample_or_average_ambiguous','food_kind_unspecified',
        'alias_requires_confirmation','alias_requires_confirmation','tfda_sample_not_found']


def test_exact_processed_sample_keeps_its_original_unspecified_state(tmp_path):
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps([sample('oil','橄欖油','unspecified')]))
    result=build_food_id_mappings([dict(food_id='FOOD000001',names=['橄欖油'])],catalog)
    assert result['records'][0]['sample_state']=='unspecified'
    assert result['records'][0]['status']=='approved'  # Identity only, not cooking validity.


def test_registry_pins_source_snapshot_and_rejects_changed_food_identity(tmp_path):
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps([sample('avg','青蔥平均值')]))
    result=build_food_id_mappings([dict(food_id='FOOD000598',names=['青蔥'])],catalog)
    registry=tmp_path/'mapping.json';registry.write_text(json.dumps(result))
    assert load_food_id_mappings(registry,catalog)=={'FOOD000598':'avg'}
    with pytest.raises(ValueError,match='ID/name'):
        load_food_id_mappings(registry,catalog,expected_names={'FOOD000598':['不同食材']})
    catalog.write_text(json.dumps([sample('avg','青蔥平均值'),sample('other','其他')]))
    with pytest.raises(ValueError,match='snapshot'):
        load_food_id_mappings(registry,catalog)


def test_existing_explicit_t04_mapping_is_preserved_and_generic_oil_is_still_pending(tmp_path):
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps([sample('chicken','去皮雞胸肉'),sample('soy','大豆油')]))
    result=build_food_id_mappings([dict(food_id='FOOD000001',names=['雞胸肉']),dict(food_id='FOOD000002',names=['油'])],
        catalog,manual_mappings={'雞胸肉':{'ingredient_id':'chicken'},'油':{'ingredient_id':'soy'}})
    assert result['records'][0]['sample_id']=='chicken'
    assert result['records'][1]['status']=='pending'


def test_real_registry_covers_all_recipe_food_ids_and_preserves_discussion_items():
    root=Path(__file__).resolve().parents[2]
    catalog=root/'nutrition_model/data/ingredient_master.json'
    registry=root/'nutrition_model/data/food_id_tfda_mapping.json'
    document=json.loads(registry.read_text(encoding='utf-8'))
    mapping=load_food_id_mappings(registry,catalog)
    assert len(document['records'])==434
    assert mapping['FOOD000598']=='E23001'
    assert mapping['FOOD001566']=='K01001'
    assert mapping['FOOD001385']=='I03104'
    assert 'FOOD000472' not in mapping and 'FOOD001036' not in mapping
