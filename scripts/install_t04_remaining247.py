"""Install only explicit pending source hypotheses and report all 247 decisions."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
from app.models import Recipe,RecipeDataRelease,IngredientAlias
from app.models.nutrition import NutritionSource,NutritionDataRelease
from app.models.nutrition_candidates import NutritionSourceCandidate
from app.services.t04_source_review import load_plan,build_reviewed_candidates,POLICY
from app.services.t04_data_feedback import recipe_feedback
from scripts.t04_db_support import guard,snapshot,HEAD


def render(report,path):
    def safe(value): return str(value).replace('|','／').replace('\n',' ')
    lines=['# T04：剩餘 247 食材來源追查','',
        '日期：2026-11-03。所有項目仍待審，未合併 Food_ID、核准 mapping、修改既有觀測或將缺值補零。','',
        f"逐項追查 {report['backlog_count']} 個食材；{report['foods_with_research_candidates']} 個有本輪比較候選，{report['foods_without_new_candidate']} 個未建立新候選。",
        '候選包含需確認品種、部位、生熟或產品配方的比較樣品，不等於已完成食材對應。','',
        f"全部 432 食材的回饋分類：`{json.dumps(report['all_food_statuses'],ensure_ascii=False)}`。",
        f"資料庫寫入：{report['committed']}；本次新增候選 {report['created_candidates']} 筆；本輪候選總數 {report['planned_candidates']} 筆。",'',
        '數值仍取自原本固定 FOOD1018 批次。另下載的 TFDA 官方快照只用於樣品名稱／俗名／內容物核對，沒有把新舊批次數值拼接。',
        '[TFDA 官方資料集](https://data.gov.tw/dataset/8543)；實際官方 ZIP、SHA-256、完整計畫見 `data/nutrition/t04-remaining247-20261103/`。',
        '既有 USDA 來源為原批次參考；一般食鹽／細砂糖的真實 0 保留，不把這些值補到海鹽／冰糖或其他食材。','',
        '## 還找不到直接候選、原來源仍缺數值，或需要材料／產品資料','',
        '「找不到」限於本輪 TFDA 2,213 樣品及原批次 374 USDA 觀測的檢索範圍，不宣稱所有資料庫均無此食品。',
        '品牌官網找到產品入口但未取得適合的每 100 g 標示時，仍列為待確認；不從零售頁或每份／每 100 ml 值猜算每 100 g。','']
    for title,items in [('',[r for r in report['items'] if not r['candidate_values_available']]),
                        ('## 找到比較來源，但需要你確認食材規格',[r for r in report['items'] if r['candidate_values_available'] and r['confirmation_required']]),
                        ('## 名稱／字形或切法候選，仍待正式來源審核',[r for r in report['items'] if r['candidate_values_available'] and not r['confirmation_required']])]:
        if title:lines.extend([title,''])
        lines.extend(['| Food_ID／食材 | 食譜 | 候選樣品與來源 | 仍缺／需確認 |','|---|---|---|---|'])
        for row in items:
            samples=[]
            for s in row['new_source_candidates']+row.get('existing_source_references',[]):
                label=f"{s['source_key']} {s['name']}（熱量 {s['per_100g']['energy_kcal'] or '未知'}；蛋白質 {s['per_100g']['protein_g'] or '未知'}）"
                samples.append(f"[{safe(label)}]({s['url']})" if s['url'] else safe(label))
            samples.extend(f"[原廠入口]({link['url']})（{safe(link['finding'])}）" for link in row.get('research_links',[]))
            lines.append('| '+' | '.join([safe(row['food_id']+' '+row['name']),safe('、'.join(row['recipe_ids'])),
                '；'.join(samples) or '本輪未建立新候選',safe(row['question'])])+' |')
        lines.append('')
    lines.extend(['## 使用方式與限制','',
        '所有逐樣品七項數值、缺值、生熟與來源 ID 在同名 JSON 報告；每一份樣品分開展示，不取平均、不跨樣品補值。',
        '前端會在需確認的來源旁顯示確認原因，這些參考樣品不列入「必要來源已找到」狀態。正式計算仍不採用任何未核准資料。',
        '原始配方／品牌與規格確認後，才可繼續尋找更合適的觀測；這份清單本身不構成核准或取代關係。',''])
    path.write_text('\n'.join(lines),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();plan=load_plan(args.plan,args.sha256)
    from app.core.config import settings
    engine=create_engine(settings.database_url);guard(engine)
    if args.apply:
        backup=ROOT/'.test-runtime/t04-remaining247/before.sql'
        meta=ROOT/'docs/1102_V3/T04/remaining247-backup.json'
        backup.parent.mkdir(parents=True,exist_ok=True)
        if not backup.exists():
            with backup.open('wb') as out:
                subprocess.run(['docker','exec','overcooked-1102-v3-db','pg_dump','-U','postgres','-d',
                    'meal_planner_1102_v3','--no-owner','--no-privileges'],stdout=out,check=True)
            meta.write_text(json.dumps({'database':'meal_planner_1102_v3','path':str(backup.relative_to(ROOT)),
                'sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),'bytes':backup.stat().st_size},indent=2)+'\n')
        elif not meta.exists() or hashlib.sha256(backup.read_bytes()).hexdigest()!=json.loads(meta.read_text())['sha256']:
            raise ValueError('Existing pre-supplement backup is not verified')
    with Session(engine) as db,db.begin() as transaction:
        if db.scalar(text('SELECT version_num FROM alembic_version'))!=HEAD: raise ValueError('V3 schema mismatch')
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('overcooked:t04:sources',0))"))
        before=snapshot(db.connection(),exclude={'nutrition_source_candidates'})
        recipe=db.get(RecipeDataRelease,settings.generation_recipe_release_id)
        publication=db.scalar(select(NutritionDataRelease).where(
            NutritionDataRelease.recipe_release_id==settings.generation_recipe_release_id,
            NutritionDataRelease.import_id==settings.generation_nutrition_import_id,
            NutritionDataRelease.mapping_version==settings.generation_food_mapping_version,NutritionDataRelease.active.is_(True)))
        if not recipe or not recipe.active or not publication or publication.recipe_source_sha256!=recipe.source_sha256:
            raise ValueError('Active recipe/nutrition publication mismatch')
        if recipe.source_sha256!=hashlib.sha256((ROOT/'data/recipe_dataset_1103_V3.xlsx').read_bytes()).hexdigest():
            raise ValueError('Current recipe workbook changed')
        old=[dict(r) for r in db.execute(select(NutritionSourceCandidate.__table__)).mappings()]
        catalog=[dict(r) for r in db.execute(select(IngredientAlias.__table__).where(
            IngredientAlias.recipe_release_id==recipe.release_id)).mappings()]
        sources=[dict(r) for r in db.execute(select(NutritionSource.__table__).where(
            NutritionSource.import_id==publication.import_id,NutritionSource.provider.in_(['TFDA','USDA']))).mappings()]
        rows=build_reviewed_candidates(plan,sources,catalog,recipe.release_id,publication.import_id,args.sha256)
        created=0
        for row in rows:
            existing=db.get(NutritionSourceCandidate,row['candidate_id'])
            if existing:
                if any(getattr(existing,k)!=v for k,v in row.items()): raise ValueError('Existing research evidence changed')
            else:db.add(NutritionSourceCandidate(**row));created+=1
        db.flush()
        assert snapshot(db.connection(),exclude={'nutrition_source_candidates'})==before
        for record in old:
            assert all(getattr(db.get(NutritionSourceCandidate,record['candidate_id']),k)==v for k,v in record.items())
        # Read through the same service as the frontend; keep all foods and
        # food-level membership, not a recipe-line count masquerading as foods.
        recipes=list(db.scalars(select(Recipe.recipe_id).where(Recipe.recipe_release_id==recipe.release_id).order_by(Recipe.recipe_id)))
        feedback={}
        for offset in range(0,len(recipes),10):
            result=recipe_feedback(db,recipes[offset:offset+10],recipe_release_id=recipe.release_id,
                import_id=publication.import_id,mapping_version=publication.mapping_version)
            assert result['adoptable'] is False and result['nutrition_totals'] is None
            for line in result['ingredients']:feedback[line['food_id']]=line
        lookup={s['source_id']:s for s in sources};selected=defaultdict(set)
        for row in rows:selected[row['food_id']].add(row['source_id'])
        items=[]
        for item in plan['items']:
            line=feedback[item['food_id']];details=[]
            for s in line['sources']:
                if s['source_id'] in selected[item['food_id']]:
                    details.append({'source_id':s['source_id'],'source_key':lookup[s['source_id']]['source_key'],
                        'name':s['name'],'provider':s['provider'],'url':s['source_url'],'state':s['state'],
                        'per_100g':s['per_100g'],'missing_fields':[k for k,v in s['per_100g'].items() if v is None],
                        'included_in_original_aggregate':s['included_in_original_aggregate']})
            prior=[]
            for s in line['sources']:
                if s['provider'] in {'TFDA','USDA'} and s['source_id'] not in selected[item['food_id']]:
                    prior.append({'source_id':s['source_id'],'source_key':lookup[s['source_id']]['source_key'],
                        'name':s['name'],'provider':s['provider'],'url':s['source_url'],'state':s['state'],
                        'per_100g':s['per_100g'],'missing_fields':[k for k,v in s['per_100g'].items() if v is None],
                        'included_in_original_aggregate':s['included_in_original_aggregate']})
            items.append({**item,'feedback_status':line['status'],'existing_source_references':prior,
                'required_values_available':line['required_values_available'],
                'candidate_values_available':line['candidate_values_available'],'new_source_candidates':details})
        report={'policy_version':POLICY,'recipe_release_id':recipe.release_id,'nutrition_import_id':publication.import_id,
            'plan_sha256':args.sha256,'official_snapshot':plan['official_snapshot'],'backlog_count':len(items),
            'planned_candidates':len(rows),'created_candidates':created,
            'foods_with_research_candidates':sum(bool(r['source_keys']) for r in items),
            'foods_without_new_candidate':sum(not r['source_keys'] for r in items),
            'backlog_statuses':dict(Counter(r['feedback_status'] for r in items)),
            'all_food_statuses':dict(Counter(r['status'] for r in feedback.values())),
            'requires_user_confirmation':sum(r['confirmation_required'] for r in items),
            'preexisting_candidates_unchanged':True,'other_37_tables_unchanged':True,
            'original_table_digests':before['table_digests'],'automatic_merge':False,'automatic_approval':False,
            'automatic_nutrient_fill':False,'committed':args.apply,'items':items}
        if not args.apply:transaction.rollback()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    render(report,args.report.with_suffix('.md'))
    print(json.dumps({k:v for k,v in report.items() if k not in {'items','original_table_digests','official_snapshot'}},ensure_ascii=False))


if __name__=='__main__':main()
