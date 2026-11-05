"""Read-only activation inventory for the actual V3 recipe ingredients."""
import json
import sys
from pathlib import Path
from collections import defaultdict, Counter

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from sqlalchemy import create_engine,text
from scripts.t04_db_support import guard,snapshot

FIELDS={'熱量(kcal/100g)':'energy_kcal','蛋白質(g/100g)':'protein_g',
        '脂肪(g/100g)':'fat_g','碳水化合物(g/100g)':'carbohydrate_g',
        '糖(g/100g)':'sugars_g','鈉(mg/100g)':'sodium_mg','膳食纖維(g/100g)':'dietary_fiber_g'}
REQUIRED=('energy_kcal','protein_g')


def assess_candidates(candidates):
    eligible=[r for r in candidates if r['provider']=='TFDA' and r['included_in_calculation'] is True]
    complete=[r for r in eligible if not any(r['values'][f] is None for f in REQUIRED)]
    if complete: reason='有完整TFDA必要數值候選，待適用性與映射核對'
    elif eligible: reason='已納入TFDA來源仍缺熱量或蛋白質，或分散在不同樣品'
    elif any(r['provider']=='TFDA' for r in candidates): reason='僅有被排除的TFDA來源關聯'
    elif any(r['provider']=='USDA' and r['included_in_calculation'] is True
             and all(r['values'][f] is not None for f in REQUIRED) for r in candidates):
        reason='有USDA必要數值候選；目前正式TFDA路徑未採用'
    elif candidates and all(r['provider']=='recipe_dataset' for r in candidates):
        reason='目前僅連到歷史食譜推導來源，不能當官方每100g樣品'
    else: reason='目前沒有可用的官方必要數值來源關聯'
    return {'included_tfda_source_links':len(eligible), 'source_gap_reason':reason,
        'tfda_links_with_energy_and_protein':len(complete),
        'required_fields_absent_from_all_included_tfda_sources':[
            f for f in REQUIRED if not any(r['values'][f] is not None for r in eligible)],
        'meaning':'Numeric availability only; not sample suitability, approval or cooking validation'}


def main():
    from app.core.config import settings
    folder=ROOT/'docs/1102_V3/T04'
    engine=create_engine(settings.database_url);guard(engine)
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        before=snapshot(c)
        publication=c.execute(text('SELECT * FROM nutrition_data_releases WHERE active AND recipe_release_id=:r'),
            {'r':settings.generation_recipe_release_id}).mappings().one()
        assert publication['import_id']==settings.generation_nutrition_import_id
        assert publication['mapping_version']==settings.generation_food_mapping_version
        params={'r':publication['recipe_release_id'],'i':publication['import_id'],'m':publication['mapping_version']}
        catalog=dict(c.execute(text('SELECT food_id,canonical_name FROM ingredient_aliases WHERE recipe_release_id=:r'),params).all())
        recipes={r['recipe_id']:dict(r) for r in c.execute(text('SELECT recipe_id,recipe_name,original_servings,'
            'servings_raw,servings_basis,cooking_methods FROM recipes WHERE recipe_release_id=:r ORDER BY recipe_id'),params).mappings()}
        uses=defaultdict(list)
        for row in c.execute(text('SELECT recipe_ingredient_id,recipe_id,food_id,canonical_name,weight_g,weight_basis,raw_text '
                'FROM recipe_ingredients WHERE recipe_release_id=:r ORDER BY recipe_id,sequence'),params).mappings():
            uses[row['food_id']].append(dict(row))
        observations=defaultdict(list)
        for row in c.execute(text('SELECT * FROM food_nutrition WHERE import_id=:i ORDER BY source_row'),params).mappings():
            observations[row['food_id']].append(dict(row))
        links=defaultdict(list)
        for row in c.execute(text('SELECT l.nutrition_id,l.included_in_calculation,s.source_id,s.provider,s.source_record_id,'
                's.source_url,s.source_sheet,s.source_row,s.raw_data FROM nutrition_source_links l '
                'JOIN nutrition_sources s ON s.source_id=l.source_id WHERE s.import_id=:i ORDER BY s.source_id'),params).mappings():
            raw=row['raw_data']
            values={field:(None if raw.get(col) is None or raw.get(col)=='' else raw[col]) for col,field in FIELDS.items()}
            links[row['nutrition_id']].append({k:v for k,v in dict(row).items() if k!='raw_data'} |
                {'original_name':raw.get('中文原名') or raw.get('來源原文名稱') or raw.get('canonical_name'),
                 'source_state':raw.get('熟製狀態'),'source_method':raw.get('烹飪方式'),
                 'source_preparation':raw.get('食用前處理'),'values':values})
        mappings=defaultdict(list)
        for row in c.execute(text('SELECT food_id,mapping_id,nutrition_id,source_id,approval_status,cooking_state,'
                'cooking_method,preparation FROM nutrition_food_mappings WHERE recipe_release_id=:r '
                'AND import_id=:i AND mapping_version=:m ORDER BY mapping_id'),params).mappings():
            mappings[row['food_id']].append(dict(row))
        receipt_lines=set(c.execute(text('SELECT recipe_ingredient_id FROM t04_cooking_receipts WHERE '
                'recipe_release_id=:r AND nutrition_import_id=:i AND mapping_version=:m'),params).scalars())
        items=[]
        for fid,lines in sorted(uses.items()):
            obs=observations[fid]
            candidates=[]
            for o in obs:
                for source in links[o['nutrition_id']]:
                    candidates.append({**source,'observation_name':o['standard_name'],
                        'observation_state':o['cooking_state'],'observation_method':o['cooking_method'],
                        'observation_preparation':o['preparation'],
                        'aggregate_missing_required':[f for f in REQUIRED if o[f] is None]})
            assessment=assess_candidates(candidates)
            approval=Counter(m['approval_status'] for m in mappings[fid])
            if not obs: category='缺少整筆營養觀測'
            elif not assessment['tfda_links_with_energy_and_protein']: category='尚無納入計算且熱量蛋白質齊全的TFDA來源候選'
            elif not approval['approved']: category='已有必要數值來源候選；待樣品映射核對'
            else: category='已有核准映射；仍須核對逐列狀態與烹調依據'
            items.append({'food_id':fid,'current_name':catalog[fid],
                'recipe_ingredient_names':sorted({l['canonical_name'] for l in lines}),
                'recipe_count':len({l['recipe_id'] for l in lines}),'recipe_line_count':len(lines),
                'observation_count':len(obs),'mapping_status_counts':dict(approval),
                'observation_missing_counts':{f:sum(o[f] is None for o in obs) for f in FIELDS.values()},
                **assessment,'category':category,'source_candidates':candidates,'mapping_candidates':mappings[fid],
                'recipe_uses':[{**line,'recipe_name':recipes[line['recipe_id']]['recipe_name'],
                    'recipe_methods':recipes[line['recipe_id']]['cooking_methods'],
                    'servings_raw':recipes[line['recipe_id']]['servings_raw'],
                    'servings_basis':recipes[line['recipe_id']]['servings_basis'],
                    'has_version_bound_receipt':line['recipe_ingredient_id'] in receipt_lines} for line in lines],
                'next_actions':['核對確切樣品、來源納入狀態、生熟、前處理與料理方式；本表不自動選樣品',
                    '依已確認的烹調前總克數核對適用重量基準；pre_cooking 不可未經接線確認直接當 cooked',
                    '逐列判定是否需要生熟轉換；需要時補上可追溯產率及保留率並綁定收據']})
        byfood={r['food_id']:r for r in items}
        recipe_rows=[]
        all_lines=[u for lines in uses.values() for u in lines]
        for rid,recipe in recipes.items():
            lines=[u for u in all_lines if u['recipe_id']==rid]
            foods=sorted({u['food_id'] for u in lines})
            recipe_rows.append({**recipe,'food_ids':foods,'ingredient_line_count':len(lines),
                'missing_observation_food_ids':[f for f in foods if not byfood[f]['observation_count']],
                'no_complete_tfda_source_candidate_food_ids':[f for f in foods if not byfood[f]['tfda_links_with_energy_and_protein']],
                'no_approved_mapping_food_ids':[f for f in foods if not byfood[f]['mapping_status_counts'].get('approved',0)],
                'receipt_line_count':sum(u['recipe_ingredient_id'] in receipt_lines for u in lines),
                'invalid_or_missing_weight_line_ids':[u['recipe_ingredient_id'] for u in lines if u['weight_g'] is None or u['weight_g']<=0],
                'status':'待逐列來源與烹調驗證；未執行正式配方營養或±10%計算'})
        assert len(items)==432 and len(recipe_rows)==293 and len(all_lines)==2477
        assert snapshot(c)==before
        c.rollback()
    summary={'food_ids':len(items),'recipes':len(recipes),'ingredient_lines':len(all_lines),
        'food_categories':dict(Counter(r['category'] for r in items)),
        'source_gap_reasons':dict(Counter(r['source_gap_reason'] for r in items if r['observation_count'])),
        'foods_with_approved_mapping':sum(bool(r['mapping_status_counts'].get('approved')) for r in items),
        'recipes_with_any_missing_observation':sum(bool(r['missing_observation_food_ids']) for r in recipe_rows),
        'recipes_with_no_complete_tfda_source_candidate':sum(bool(r['no_complete_tfda_source_candidate_food_ids']) for r in recipe_rows),
        'recipes_with_any_unapproved_food':sum(bool(r['no_approved_mapping_food_ids']) for r in recipe_rows),
        'version_bound_receipt_lines':sum(r['receipt_line_count'] for r in recipe_rows),
        'invalid_or_missing_weight_lines':sum(len(r['invalid_or_missing_weight_line_ids']) for r in recipe_rows),
        'database_read_only':True,'all_table_digests_unchanged':True,'external_models_called':False,
        'automatic_selection_merge_approval_or_fill':False}
    payload={'recipe_release_id':params['r'],'nutrition_import_id':params['i'],'mapping_version':params['m'],
             'summary':summary,'foods':items,'recipes':recipe_rows}
    (folder/'activation-gaps-432.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    def esc(v): return str('—' if v is None or v=='' else v).replace('|','／').replace('\n','；')
    lines=['# T04：現行432個食材啟用缺口','',
        '只讀 V3；未選樣品、合併、改名、核准、補值或呼叫模型。完整候選樣品、數值與來源網址及使用列見 activation-gaps-432.json。','',
        '## 本輪結論','']
    for label,value in summary['food_categories'].items(): lines.append(f'- {label}：{value} 個食材。')
    lines += ['', '來源關聯細分（不自動改變來源政策或映射）：']
    for label,value in summary['source_gap_reasons'].items(): lines.append(f'- {label}：{value} 個食材。')
    lines += [f"- 有未核准食材的食譜：{summary['recipes_with_any_unapproved_food']}／293。",
        f"- 有整筆觀測缺口的食譜：{summary['recipes_with_any_missing_observation']}；有必要數值來源候選缺口的食譜：{summary['recipes_with_no_complete_tfda_source_candidate']}（可能重疊）。",
        f"- 缺少或非正數克數：{summary['invalid_or_missing_weight_lines']} 列；已綁版烹調收據：{summary['version_bound_receipt_lines']} 列。",'',
        '來源有熱量／蛋白質僅代表數值可供核對，沒有判定樣品適用或批准使用。TFDA統計只計納入計算的原始來源；USDA及歷史配方推導另外保留在詳細候選中，不自動代替TFDA。', '',
        '缺口以目前同一Food_ID的來源關聯為範圍；其他Food_ID或舊catalog中可能有相關官方樣品，仍需人工核對，不代表必須重新下載同數量的新資料。', '',
        '每道菜的料理方式與每個食材原有生熟／加工狀態需逐列核對。收據為0不能直接解讀為所有食材都必須估係數；符合直接使用條件的資料不應重複轉換。', '',
        '## 432個食材完整盤點','',
        '| Food_ID | 現行食材 | 觀測數 | 有熱量與蛋白質的納入TFDA來源連結數 | 核准映射數 | 使用菜數 | 目前缺口 |',
        '|---|---|---:|---:|---:|---:|---|']
    for r in items:
        lines.append('| '+' | '.join(map(esc,[r['food_id'],r['current_name'],r['observation_count'],
            r['tfda_links_with_energy_and_protein'],r['mapping_status_counts'].get('approved',0),r['recipe_count'],r['category']]))+' |')
    lines += ['', '## 優先處理的來源缺口','']
    for r in items:
        if not r['tfda_links_with_energy_and_protein']:
            names=sorted({u['recipe_id']+' '+u['recipe_name'] for u in r['recipe_uses']})
            lines.append(f"- **{r['food_id']} {r['current_name']}**：{r['category']}。受影響：{'、'.join(names)}。")
    lines += ['', '## 逐道食譜核對清單','',
        '| 食譜ID | 菜名 | 原始份數／批次 | 整筆觀測缺口 | 無完整TFDA必要數值候選的食材 | 未核准映射食材數 |',
        '|---|---|---|---|---|---:|']
    for r in recipe_rows:
        lines.append('| '+' | '.join(map(esc,[r['recipe_id'],r['recipe_name'],r['servings_raw']+'／'+r['servings_basis'],
            '、'.join(r['missing_observation_food_ids']),'、'.join(r['no_complete_tfda_source_candidate_food_ids']),len(r['no_approved_mapping_food_ids'])]))+' |')
    lines += ['', '## 建議處理順序','',
        '1. 先核對整筆來源與必要數值缺口；AMF無來源維持未知，不拿其他油脂補。',
        '2. 逐一確認現行配方適用的樣品、生熟、前處理與料理方式，保留多義候選，不自動核准。',
        '3. 處理重量基準接線及特殊份數：pre_cooking 是使用者確認的整份配方烹調前重量，需明確對應計算器的 raw／cooked；1批不當1人份。',
        '4. 只有確認需要生熟轉換的列才準備產率／保留率證據與版本收據；本輪沒有自動估算。',
        '5. 樣品與依據齊備後才逐道執行T04→T12計算與±10%驗證。本表不是計算通過或採用核准。', '',
        '目前81個主檔名稱差異均不在這432個現行配方Food_ID中；不將它們列為當前配方啟用障礙。']
    (folder/'ACTIVATION-GAPS-432.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))
    engine.dispose()


if __name__=='__main__': main()
