"""Additional source bindings and frontend data feedback, without adoption approval."""
from collections import defaultdict, Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
import unicodedata
from urllib.parse import urlparse

from sqlalchemy import select
from app.models import Recipe, RecipeIngredient, IngredientAlias, RecipeDataRelease
from app.models.nutrition import NutritionDataRelease, NutritionSource, FoodNutrition, NutritionSourceLink
from app.models.nutrition_candidates import NutritionSourceCandidate, NutritionProductSource

POLICY='t04-official-source-candidates-v1'
COLUMNS={'熱量(kcal/100g)':'energy_kcal','蛋白質(g/100g)':'protein_g','脂肪(g/100g)':'fat_g',
         '碳水化合物(g/100g)':'carbohydrate_g','糖(g/100g)':'sugars_g','鈉(mg/100g)':'sodium_mg','膳食纖維(g/100g)':'dietary_fiber_g'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def normalize(value):
    return unicodedata.normalize('NFKC',str(value or '')).strip().casefold()


def numeric(value):
    if value is None or isinstance(value,bool) or value=='': return None
    try: number=Decimal(str(value))
    except (InvalidOperation,ValueError): return None
    return str(number) if number.is_finite() and number>=0 else None


def per100(raw):
    return {field:numeric(raw.get(column)) for column,field in COLUMNS.items()}


def build_candidates(catalog,sources,legacy_master,legacy_hash,recipe_release_id,import_id):
    """Exact names/declared aliases only; all matches remain candidate evidence."""
    legacy={r['ingredient_id']:r for r in legacy_master}
    output=[]
    for food in catalog:
        tokens={normalize(food['canonical_name']):'canonical'}
        tokens.update({normalize(a):'current_dictionary_alias' for a in food.get('aliases',[]) if isinstance(a,str) and a.strip()})
        for source in sources:
            if source['provider']!='TFDA' or source['import_id']!=import_id: continue
            raw=source['raw_data']
            names={normalize(raw.get('中文原名')),normalize(raw.get('來源原文名稱'))}-{''}
            matches=[]
            for token,kind in tokens.items():
                if token in names: matches.append({'token':token,'kind':kind})
                if token+'平均值' in names: matches.append({'token':token,'kind':kind+'_official_average_name'})
            record=legacy.get(source['source_record_id'],{})
            if record.get('source',{}).get('provider')=='TFDA':
                aliases={normalize(a) for a in record.get('aliases',[]) if isinstance(a,str)}
                for token in tokens:
                    if token in aliases: matches.append({'token':token,'kind':'legacy_declared_alias_candidate'})
            if not matches: continue
            evidence={'canonical_name':food['canonical_name'],'dictionary_aliases':food.get('aliases',[]),
                'matches':matches,'legacy_master_sha256':legacy_hash,'source_name':raw.get('中文原名') or raw.get('來源原文名稱'),
                'automatic_approval':False,'automatic_nutrient_fill':False}
            fields={'recipe_release_id':recipe_release_id,'nutrition_import_id':import_id,'food_id':food['food_id'],
                'source_id':source['source_id'],'source_fingerprint':fingerprint(source),
                'policy_version':POLICY,'match_evidence':evidence,'review_status':'pending_review'}
            output.append({'candidate_id':fingerprint(fields),**fields})
    return output


def source_dto(source,kind,included,evidence=None):
    raw=source.raw_data
    url=source.source_url
    if url and urlparse(url).scheme not in {'http','https'}: url=None
    return {'source_id':source.source_id,'provider':source.provider,'sample_id':source.source_record_id,
        'name':raw.get('中文原名') or raw.get('來源原文名稱') or raw.get('canonical_name') or source.source_record_id,
        'source_url':url,'per_100g':(per100(raw) if source.provider in {'TFDA','USDA'}
            else {f:None for f in COLUMNS.values()}),'state':raw.get('熟製狀態'),
        'cooking_method':raw.get('烹飪方式'),'preparation':raw.get('食用前處理'),
        'included_in_original_aggregate':included,'binding_kind':kind,'review_status':'pending_review',
        'confirmation_required':bool((evidence or {}).get('confirmation_required')),
        'confirmation_question':(evidence or {}).get('confirmation_question'),
        'match_evidence':evidence,'value_basis':'source_per_100g' if source.provider in {'TFDA','USDA'} else 'historical_recipe_evidence_not_per_100g'}


def recipe_feedback(db,recipe_ids,*,recipe_release_id,import_id,mapping_version):
    publication=db.scalar(select(NutritionDataRelease).where(NutritionDataRelease.recipe_release_id==recipe_release_id,
        NutritionDataRelease.import_id==import_id,NutritionDataRelease.mapping_version==mapping_version,NutritionDataRelease.active.is_(True)))
    if publication is None: raise ValueError('NUTRITION_VERSION_REQUIRED')
    release=db.get(RecipeDataRelease,recipe_release_id)
    if not release or not release.active or release.source_sha256!=publication.recipe_source_sha256:
        raise ValueError('RECIPE_NUTRITION_RELEASE_MISMATCH')
    recipes=list(db.scalars(select(Recipe).where(Recipe.recipe_id.in_(recipe_ids),Recipe.recipe_release_id==recipe_release_id)))
    if {r.recipe_id for r in recipes}!=set(recipe_ids): raise ValueError('RECIPE_NUTRITION_RELEASE_MISMATCH')
    lines=list(db.scalars(select(RecipeIngredient).where(RecipeIngredient.recipe_id.in_(recipe_ids),
        RecipeIngredient.recipe_release_id==recipe_release_id).order_by(RecipeIngredient.recipe_id,RecipeIngredient.sequence)))
    ids={r.food_id for r in lines}; byfood=defaultdict(dict)
    original=db.execute(select(FoodNutrition.food_id,NutritionSource,NutritionSourceLink.included_in_calculation)
        .join(NutritionSourceLink,NutritionSourceLink.nutrition_id==FoodNutrition.nutrition_id)
        .join(NutritionSource,NutritionSource.source_id==NutritionSourceLink.source_id)
        .where(FoodNutrition.import_id==import_id,NutritionSource.import_id==import_id,FoodNutrition.food_id.in_(ids))).all()
    for fid,source,included in original:
        byfood[fid][source.source_id]=source_dto(source,'original_food_link',included)
    additions=db.execute(select(NutritionSourceCandidate,NutritionSource).join(NutritionSource,
        NutritionSource.source_id==NutritionSourceCandidate.source_id).where(
        NutritionSourceCandidate.recipe_release_id==recipe_release_id,NutritionSourceCandidate.nutrition_import_id==import_id,
        NutritionSource.import_id==import_id,NutritionSourceCandidate.food_id.in_(ids))).all()
    for candidate,source in additions:
        if fingerprint({c.name:getattr(source,c.name) for c in NutritionSource.__table__.columns})!=candidate.source_fingerprint:
            raise ValueError('NUTRITION_SOURCE_CHANGED')
        original=byfood[candidate.food_id].get(source.source_id)
        included=(original['included_in_original_aggregate'] if original else
            source.raw_data.get('納入平均') in ('是','Y','yes',True))
        byfood[candidate.food_id][source.source_id]=source_dto(source,'additional_source_candidate',
            included,candidate.match_evidence)
    products=list(db.scalars(select(NutritionProductSource).where(
        NutritionProductSource.recipe_release_id==recipe_release_id,NutritionProductSource.food_id.in_(ids))))
    for product in products:
        if (fingerprint(product.provenance)!=product.product_source_id
                or product.per_100g!={f:numeric(product.provenance.get('values',{}).get(f)) for f in COLUMNS.values()}
                or product.evidence_sha256!=product.provenance.get('label_sha256')
                or product.source_url!=product.provenance.get('source_url')):
            raise ValueError('NUTRITION_SOURCE_CHANGED')
        byfood[product.food_id][product.product_source_id]={'source_id':product.product_source_id,
            'provider':'MANUFACTURER_LABEL','sample_id':product.source_name,'name':product.source_name,
            'source_url':product.source_url,'per_100g':product.per_100g,'state':'as_sold','cooking_method':None,
            'preparation':None,'included_in_original_aggregate':None,'binding_kind':'user_identified_product_label',
            'review_status':'label_transcribed_not_cooking_validated','match_evidence':product.provenance,
            'value_basis':'manufacturer_label_per_100g_as_sold'}
    output=[]
    for line in lines:
        candidates=list(byfood[line.food_id].values())
        numeric_candidates=[s for s in candidates if (s['provider'] in {'TFDA','USDA','MANUFACTURER_LABEL'}
            and s['included_in_original_aggregate'] is not False
            and s['per_100g'].get('energy_kcal') is not None and s['per_100g'].get('protein_g') is not None)]
        usable=[s for s in numeric_candidates if not s.get('confirmation_required',False)]
        status=('source_available_pending_review' if usable else 'source_candidate_needs_confirmation' if numeric_candidates
            else 'source_values_incomplete' if candidates else 'source_missing')
        output.append({'recipe_id':line.recipe_id,'line_id':line.recipe_ingredient_id,'food_id':line.food_id,
            'name':line.canonical_name,'whole_recipe_pre_cooking_grams':str(line.weight_g) if line.weight_g is not None else None,
            'weight_basis':line.weight_basis,'status':status,'sources':candidates,
            'required_values_available':bool(usable),'candidate_values_available':bool(numeric_candidates),'nutrition_totals':None,
            'blockers':['source_selection_and_state_review_required','cooking_evidence_not_validated']})
    return {'status':'data_review_required','adoptable':False,'nutrition_totals':None,
        'message':'來源資料已列出；尚未完成樣品、生熟及烹調核對，不代表營養計算通過。',
        'versions':{'recipe_release_id':recipe_release_id,'nutrition_import_id':import_id,'mapping_version':mapping_version},
        'recipes':[{'recipe_id':r.recipe_id,'name':r.recipe_name,'servings_raw':r.servings_raw,
                    'servings_basis':r.servings_basis} for r in recipes],
        'summary':dict(Counter(r['status'] for r in output)), 'ingredients':output}
