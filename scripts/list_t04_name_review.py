"""Export current T04 naming/AMF review evidence using a read-only V3 transaction."""
import json
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from sqlalchemy import create_engine, text
from scripts.t04_db_support import guard, snapshot


def main():
    from app.core.config import settings
    engine = create_engine(settings.database_url)
    guard(engine)
    folder = ROOT/'docs/1102_V3/T04'
    base = json.loads((folder/'mapping-review.json').read_text(encoding='utf-8'))
    imported = json.loads((folder/'import-result.json').read_text(encoding='utf-8'))
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        before = snapshot(c)
        release = c.execute(text('SELECT release_id,source_sha256 FROM recipe_data_releases WHERE active')).mappings().one()
        assert release['release_id'] == imported['recipe_release_id']
        catalog = dict(c.execute(text('SELECT food_id,canonical_name FROM ingredient_aliases WHERE recipe_release_id=:r'),
                                {'r':release['release_id']}).all())
        uses = defaultdict(list)
        for row in c.execute(text('SELECT i.food_id,i.canonical_name,i.recipe_ingredient_id,i.weight_g,i.weight_basis,'
                'i.raw_text,r.recipe_id,r.recipe_name,r.original_servings,r.servings_raw,r.servings_basis '
                'FROM recipe_ingredients i JOIN recipes r ON r.recipe_id=i.recipe_id '
                'WHERE i.recipe_release_id=:r AND r.recipe_release_id=:r ORDER BY r.recipe_id,i.recipe_ingredient_id'),
                {'r':release['release_id']}).mappings():
            uses[row['food_id']].append(dict(row))
        observations = {row['source_row']:dict(row) for row in c.execute(text(
            'SELECT * FROM food_nutrition WHERE import_id=:i'),{'i':imported['import_id']}).mappings()}
        sources = defaultdict(list)
        for row in c.execute(text('SELECT l.nutrition_id,l.included_in_calculation,s.provider,s.source_record_id,'
                's.source_url,s.source_row,s.source_sheet,s.raw_data FROM nutrition_source_links l '
                'JOIN nutrition_sources s ON s.source_id=l.source_id WHERE s.import_id=:i '
                'ORDER BY s.provider,s.source_record_id'),{'i':imported['import_id']}).mappings():
            d=dict(row); raw=d.pop('raw_data')
            d['original_name']=(raw.get('中文原名') or raw.get('來源原文名稱') or raw.get('canonical_name')
                                or raw.get('標準食材名稱'))
            d['raw_data']=raw
            sources[d['nutrition_id']].append(d)
        rows=[]
        for item in sorted(base['name_differences'],key=lambda r:r['food_id']):
            n=observations[item['source_row']]
            assert n['food_id']==item['food_id'] and n['standard_name']==item['nutrition_name']
            assert catalog[item['food_id']]==item['current_name']
            rows.append({**item,'recipe_ingredient_names':sorted({u['canonical_name'] for u in uses[item['food_id']]}),
                'recipe_count':len({u['recipe_id'] for u in uses[item['food_id']]}),
                'recipe_uses':uses[item['food_id']], 'cooking_state':n['cooking_state'],
                'cooking_method':n['cooking_method'],'preparation':n['preparation'],
                'original_sources':sources[n['nutrition_id']], 'decision':'待人工核對；未合併、改名或補值'})
        assert len(rows)==81 and len({r['food_id'] for r in rows})==81
        amf_id='FOOD001993'
        assert not any(n['food_id']==amf_id for n in observations.values())
        amf={'food_id':amf_id,'current_name':catalog[amf_id],'recipe_uses':uses[amf_id],
            'missing_fields':['energy_kcal','protein_g','fat_g','carbohydrate_g','sugars_g','sodium_mg','dietary_fiber_g'],
            'missing_evidence':['每100g營養觀測','可追溯來源／產品與版本','重量與生熟／加工狀態適用依據'],
            'status':'沒有觀測，保留未知；未以奶油／其他油脂或配方既有數字替代'}
        assert snapshot(c)==before
        c.rollback()
    used=[r for r in rows if r['recipe_count']]
    result={'recipe_release_id':release['release_id'],'nutrition_import_id':imported['import_id'],
        'difference_food_ids':81,'used_difference_food_ids':len(used),
        'unused_difference_food_ids':81-len(used),'amf':amf,'rows':rows,
        'database_read_only':True,'all_table_digests_unchanged':True,'automatic_merge_or_fill':False}
    (folder/'name-review-81.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    def esc(value):
        return str('—' if value is None or value == '' else value).replace('|','／').replace('\n','；')
    lines=['# T04：81 個名稱差異及 AMF 缺資料清單','',
        '本報告只讀取現行 V3，未寫入資料庫、改名、合併、核准或補值。', '',
        f"目前食譜版本：`{release['release_id']}`；營養來源為 `All_Food_ID_1018.xlsx`。",'',
        f'共 81 個 Food_ID 名稱差異：其中 {len(used)} 個目前有食譜使用，{81-len(used)} 個僅在食材主檔、沒有現行食譜使用列。', '',
        '「目前食材名稱」取自現行食譜工作簿的食材主檔；「實際食譜用名」取自配方列。未使用的主檔項目不冒稱為實際配方。營養彙整名取自 All 分頁；原始樣品名稱與完整來源網址保存在同目錄 name-review-81.json。', '',
        '## 81 個完整對照','',
        '| # | Food_ID | 目前食材名稱 | 實際食譜用名 | 營養彙整名稱 | 原始TFDA／USDA樣品名稱 | 生熟／料理方式 | 使用菜數 | All 原列 |',
        '|---:|---|---|---|---|---|---|---:|---:|']
    for i,r in enumerate(rows,1):
        lines.append('| '+' | '.join(map(esc,[i,r['food_id'],r['current_name'],
            '、'.join(r['recipe_ingredient_names']),r['nutrition_name'],
            '；'.join(sorted({s['provider']+'：'+(s['original_name'] or s['source_record_id'])
                            for s in r['original_sources'] if s['provider'] in {'TFDA','USDA'}})),
            r['cooking_state']+'／'+(r['cooking_method'] or '未列方式'),r['recipe_count'],r['source_row']]))+' |')
    lines += ['', '## 現行食譜受影響明細','']
    for r in used:
        lines += [f"### {r['food_id']}：{r['current_name']} ↔ {r['nutrition_name']}", '']
        for u in r['recipe_uses']:
            lines.append(f"- {u['recipe_id']} {u['recipe_name']}｜{u['recipe_ingredient_id']}｜配方用名：{u['canonical_name']}｜整份配方烹調前 {u['weight_g']} g｜來源份數：{u['servings_raw']}（{u['servings_basis']}）")
        lines.append('')
    lines += ['## AMF：FOOD001993','', f"目前名稱：**{amf['current_name']}**。現行營養批次沒有任何對應觀測。",'',
        '缺少每100g的熱量、蛋白質、脂肪、碳水、糖、鈉、纖維，以及可追溯來源與適用的加工／生熟基準。缺少的是整筆來源觀測，不能理解成這七個值都是 0。', '',
        '未替換成奶油、無鹽奶油、酥油或其他油脂；也未把配方來源營養欄位當成每100g資料。', '',
        '| 食譜 | 食材列 | 配方用名 | 整份配方烹調前克數 | 原始份數／批次 |',
        '|---|---|---|---:|---|']
    for u in amf['recipe_uses']:
        lines.append('| '+' | '.join(map(esc,[u['recipe_id']+' '+u['recipe_name'],u['recipe_ingredient_id'],u['canonical_name'],u['weight_g'],u['servings_raw']+'／'+u['servings_basis']]))+' |')
    lines += ['', '## 後續整合方式','',
        '可整理成一份統一發布清冊，關聯現有食譜、營養來源、樣品／生熟方式及價格；保留各自來源與版本，不把不同樣品的數值壓成同一筆。此清單只是待審證據，編輯它不會自動寫入 DB。', '',
        '名稱相近不代表相同食材；同一 Food_ID 也不能跳過加工、生熟、前處理與重量基準確認。本輪所有項目維持待核對，AMF 維持未知。', '',
        '核對：81 個唯一 Food_ID；來源列與目前 DB 一致；讀取前後全部資料表內容 hash 相同。']
    (folder/'NAME-REVIEW-81.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'rows','amf'}},ensure_ascii=False))
    print(json.dumps({'amf_recipe_count':len({u['recipe_id'] for u in amf['recipe_uses']}),'amf_uses':amf['recipe_uses']},ensure_ascii=False,default=str))
    engine.dispose()


if __name__=='__main__':
    main()
