"""採用／不採用資料庫菜單與已採用紀錄（2026-11-03，移植自主線；demo 改用本分支的 T12 預覽與 T13 成本）。

此流程依同一版問卷重新查菜單（T11）並重算營養（t12_planning.preview，與生成工作同一套 calculate_meal）與
成本（T13 meal_cost_from_db），只有與畫面上的菜單相同、營養通過、沒有主食／禁忌阻擋、已知成本未超預算且
未撞昨日菜時才保存。T14 生成結果的採用另外由 meal_plans 處理；預算／缺價判定（meal_validation.evaluate）、
昨日重驗與快照的昨日比對與它共用同一份程式。

- 不採用：記錄這組食譜，同一版問卷下次查詢改給下一組合格組合（指定菜照樣保留），直到使用者採用。
- 採用：單一交易寫入 adopted_meal_plans（含完整快照），問卷改為 confirmed；同一問卷只採用一次，
  重送同一把 Idempotency-Key 回傳同一筆。已採用的菜會在隔天的資料庫菜單排除（T11 昨日去重）。
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError, version_conflict
from app.db.models import AdoptedMealPlan, MealMenuRejection, MealRequest
from app.services import household, meal_history, meal_plans, meal_validation, recipe_retrieval, t12_planning
from app.services import idempotency
from app.services import meal_request as request_service
from app.services.generation import _jsonable, cost_snapshot
from app.services.meal_cost import meal_cost_from_db
from app.services.recipe_import import standard_dish_id
from app.services.seasonings import load_seasonings
from app.services.synonyms import confirmed_equivalents
from app.source_adapters.price_collection import load_price_exempt

ALL_REJECTED = "all_menus_rejected"
SNAPSHOT_VERSION = "database_menu_snapshot/v2"  # v2（2026-11-08）：補 T13 成本、昨日比對、來源與規則版本


def _rejected(db: Session, row: MealRequest) -> list[set[str]]:
    rows = db.scalars(select(MealMenuRejection).where(MealMenuRejection.meal_request_id == row.id,
                                                      MealMenuRejection.request_version == row.version))
    return [set(r.recipe_ids) for r in rows]


def current_menu(db: Session, household_id, row: MealRequest, draft, members) -> dict:
    """T11 資料庫菜單，略過這一版問卷已「不採用」的組合；全部不採用時 selected 為空、status=all_menus_rejected。"""
    restrictions = request_service.restrictions(db, draft, members)
    result = recipe_retrieval.retrieve_database_menu(db, household_id, draft,
                                                     excluded_food_ids=restrictions['excluded_food_ids'])
    out = result.to_dict()
    rejected = _rejected(db, row)
    if result.status == recipe_retrieval.DATABASE_MENU and rejected:
        remaining = [combo for combo in result.alternatives
                     if {d['recipe_id'] for d in combo} not in rejected]
        out['selected'] = remaining[0] if remaining else []
        out['alternatives'] = remaining
        if not remaining:
            out['status'], out['reason'] = ALL_REJECTED, 'ALL_MENUS_REJECTED'
    out['rejected_count'] = len({frozenset(s) for s in rejected})
    return out


def reject(db: Session, row: MealRequest, *, version: int, recipe_ids: list[str], user_id) -> None:
    if row.version != version:
        raise version_conflict()
    if row.status == 'confirmed':
        raise ApiError(409, 'ALREADY_ADOPTED', '這份問卷已經採用菜單，不能再換。')
    ids = sorted(set(recipe_ids))
    if set(ids) in _rejected(db, row):
        return  # 重送或連點同一組：不重複記錄（呼叫端已鎖住問卷列）
    db.add(MealMenuRejection(id=uuid.uuid4(), meal_request_id=row.id, request_version=version,
                             recipe_ids=ids, created_by=user_id))
    db.flush()


def adopt(db: Session, *, household_id, user_id, row: MealRequest, draft, members, version: int,
          recipe_ids: list[str], idempotency_key: str) -> tuple[AdoptedMealPlan, bool]:
    """重驗版本與菜單、重算營養與成本、重讀昨日紀錄後保存；回傳 (紀錄, 是否新建)。"""
    meal_plans._lock_household(db, household_id)
    digest = idempotency.request_hash('adopt-preview', row.id, version, sorted(set(recipe_ids)))
    existing = db.scalar(select(AdoptedMealPlan).where(AdoptedMealPlan.household_id == household_id,
                                                       AdoptedMealPlan.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.meal_request_id != row.id or existing.request_hash != digest:
            raise ApiError(409, 'IDEMPOTENCY_KEY_REUSED', '這個請求已用於另一份問卷。')
        return existing, False
    if db.scalar(select(AdoptedMealPlan).where(AdoptedMealPlan.meal_request_id == row.id)) is not None:
        raise ApiError(409, 'ALREADY_ADOPTED', '這份問卷已經採用過菜單。')
    if row.version != version:
        raise version_conflict()
    if row.status in ('queued', 'running'):
        raise ApiError(409, 'VERSION_CONFLICT', '此問卷正在生成中，請等待結果。')
    if draft.meal_date is None or draft.meal_type is None:
        raise ApiError(422, 'INVALID_INPUT', '請先完成問卷。')
    menu = current_menu(db, household_id, row, draft, members)
    selected = menu.get('selected') or []
    if sorted(d['recipe_id'] for d in selected) != sorted(set(recipe_ids)):
        raise ApiError(409, 'MENU_CHANGED', '菜單已經變更，請重新查詢後再採用。')
    preview = t12_planning.preview(db, draft=draft, members=members, recipe_ids=[d['recipe_id'] for d in selected])
    # 主食含禁忌、主食沒有營養資料、菜含禁忌等阻擋（adoption_blocked）與營養未通過一樣不可採用
    if preview.get('status') != 'nutrition_passed' or preview.get('adoption_blocked', True):
        raise ApiError(409, 'NOT_ADOPTABLE', preview.get('status_message') or '這份菜單的營養沒有通過，不能採用。')
    # T13 成本與預算：與生成結果採用用同一張決策表（meal_validation.evaluate），已知成本超預算一律拒絕
    cost = _cost(db, preview, draft)
    evaluation = meal_validation.evaluate(
        {'request': {'request_version': version}, 'validation': {'hard_constraints_passed': True},
         'calculation': {'block_reasons': []}, 'cost': cost}, decisions=[], current_request_version=row.version)
    meal_plans.require_adoptable(evaluation)
    dishes = [{'standard_dish_id': d['standard_dish_id'], 'dish_name': d['recipe_name'],
               'aliases': d.get('aliases') or [], 'source': 'database', 'recipe_id': d['recipe_id']}
              for d in selected]
    context = menu.get('previous_day') or meal_history.previous_day_context(
        db, household_id, draft.meal_date, meal_type=draft.meal_type,
        specified_dish_ids=[standard_dish_id(n) for n in draft.specified_dishes])
    recheck = meal_plans.require_previous_day(db, context, dishes)
    plan = meal_history.record_adopted(db, household_id, draft.meal_date, draft.meal_type,
                                       mode='database_menu', dishes=dishes)
    plan.meal_request_id, plan.request_version = row.id, row.version
    plan.idempotency_key, plan.request_hash, plan.adopted_by = idempotency_key, digest, user_id
    plan.price_exception_accepted = evaluation['price_exception_accepted']
    snapshot = _snapshot(db, row, selected, preview, draft, cost=cost, evaluation=evaluation,
                         context=context, recheck=recheck)
    if draft.mode == 'picker':
        snapshot['market_shortages'] = _market_shortages(preview, draft.available_ingredient_ids)
    plan.snapshot = snapshot
    meal_plans.confirm_request(row)
    db.flush()
    return plan, True


def _cost(db: Session, preview: dict, draft) -> dict:
    """依 T12 驗證後的整餐用量（calc requirements，不含主食，決策 #16）計算 T13 成本；
    與生成結果的 cost 區同一格式（generation._cost_view）。克數未知的食材列入 unquantified_food_ids，不當 0。"""
    calc = preview.get('_calc') or {}
    staple_ids = {(preview.get('staple') or {}).get('food_id')} - {None}
    unquantified = sorted({r['food_id'] for r in calc.get('unresolved_ingredients') or []
                           if r['food_id'] not in staple_ids})
    if not calc.get('requirements'):
        return {'status': 'not_computed', 'reason': 'NO_FINAL_REQUIREMENTS', 'budget_status': None,
                'unquantified_food_ids': unquantified}
    cost = cost_snapshot(meal_cost_from_db(db, calc['requirements'], draft.available_ingredient_ids, draft.budget,
                                           now=datetime.now(timezone.utc)))
    cost['unquantified_food_ids'] = unquantified
    return _jsonable(cost)


def _snapshot(db: Session, row: MealRequest, selected: list[dict], preview: dict, draft, *, cost: dict,
              evaluation: dict, context: dict, recheck: dict) -> dict:
    """畫面需要的完整快照：菜（含用量與步驟）、主食、七種營養素、每人份量與每日建議、目標；
    另存與生成結果採用相同的成本、昨日比對、來源與規則版本，來源之後更新不改歷史。"""
    keep = ('status', 'status_message', 'targets', 'nutrition_all', 'dishes', 'staple', 'people',
            'daily_reference_meta', 'dish_portions')
    nutrition = preview.get('nutrition') or {}
    snapshot = {
        'schema_version': SNAPSHOT_VERSION,
        'record_label': meal_plans.RECORD_LABEL,
        'adopted_versions': {'meal_request_id': str(row.id), 'request_version': row.version,
                             'recipe_ids': sorted(d['recipe_id'] for d in selected)},
        'request': copy.deepcopy(row.payload),
        'selected': selected, 'staple_choice': draft.staple,
        'nutrition': {'totals': nutrition.get('totals')}, **{k: preview.get(k) for k in keep},
        'cost': cost,
        'recipe_sources': meal_plans._recipe_sources(db, selected),
        'eligibility': {k: evaluation[k] for k in ('adoptable', 'blocks', 'price_exception_accepted')},
        'missing': {'price_food_ids': meal_validation.missing_price_food_ids({'cost': cost}),
                    'price_exception_accepted': evaluation['price_exception_accepted'],
                    'nutrients': list(nutrition.get('missing') or [])},
        'previous_day': meal_plans.previous_day_snapshot(context, recheck),
        'versions': {'snapshot_schema_version': SNAPSHOT_VERSION,
                     'adoption_rule_version': meal_plans.ADOPTION_RULE_VERSION,
                     'nutrition_rule_version': household.target_service().rule_version,
                     # demo 的食譜沒有發布版本（recipe_dataset_1027），不冒充主線的 T01 發布
                     'recipe_release_id': None},
    }
    # JSONB 欄位：Decimal／Fraction 以字串保存（精確值，不轉成 float）
    return json.loads(json.dumps(snapshot, ensure_ascii=False, default=str))


def _market_shortages(preview: dict, available_food_ids: list[str]) -> dict:
    """凍結食譜庫選菜的整餐用量，供模擬購物；已有、調味料、免計價的食材不列，克數未知者列入 unquantified_items。"""
    free = set(confirmed_equivalents(available_food_ids)) | load_seasonings() | load_price_exempt()
    ingredients = [line for dish in preview.get('dishes') or [] for line in dish['ingredients']]
    if (staple := preview.get('staple')) and staple.get('food_id'):
        ingredients.append({'food_id': staple['food_id'], 'name': staple['name'], 'grams': staple.get('final_grams')})

    amounts: dict[str, Fraction] = {}
    unknown: set[str] = set()
    names: dict[str, str] = {}
    for line in ingredients:
        food_id = line['food_id']
        if food_id in free:
            continue
        names.setdefault(food_id, line['name'])
        grams = line.get('grams')
        if grams is None:
            unknown.add(food_id)  # 克數未知：不當 0，也不併入已知量
        else:
            amounts[food_id] = amounts.get(food_id, Fraction()) + Fraction(str(grams))

    items = [{'food_id': food_id, 'name': names[food_id], 'shortage_grams': t12_planning._text(grams)}
             for food_id, grams in amounts.items() if food_id not in unknown and grams > 0]
    return {'recipe_release_id': None,
            'items': items,
            'unquantified_items': [{'food_id': food_id, 'name': name}
                                   for food_id, name in names.items() if food_id in unknown]}


def _number(value) -> float | None:
    return None if value is None else float(Fraction(str(value)))


def summary(plan: AdoptedMealPlan) -> dict:
    snapshot = plan.snapshot or {}
    totals = (snapshot.get('nutrition') or {}).get('totals') or {}
    return {'id': str(plan.id), 'meal_date': plan.meal_date.isoformat(), 'meal_type': plan.meal_type,
            'mode': plan.mode, 'status': plan.status, 'adopted_at': plan.adopted_at.isoformat(),
            'meal_request_id': str(plan.meal_request_id) if plan.meal_request_id else None,
            'dishes': [{'dish_name': d['dish_name'], 'recipe_id': d.get('recipe_id')} for d in plan.dishes],
            'energy_kcal': totals.get('energy_kcal'), 'protein_g': totals.get('protein_g'),
            'market_shortages_available': 'market_shortages' in snapshot}


def detail(plan: AdoptedMealPlan) -> dict:
    return {**summary(plan), 'snapshot': plan.snapshot or {}}


_MEAL_ORDER = {'breakfast': 0, 'lunch': 1, 'dinner': 2}


def recent_dates(db: Session, household_id, limit: int = 60) -> list[dict]:
    plans = db.scalars(select(AdoptedMealPlan).where(AdoptedMealPlan.household_id == household_id,
                                                     AdoptedMealPlan.status == 'adopted'))
    counts: dict[date, int] = {}
    for p in plans:
        counts[p.meal_date] = counts.get(p.meal_date, 0) + 1
    return [{'meal_date': d.isoformat(), 'count': n} for d, n in sorted(counts.items(), reverse=True)[:limit]]


def on_date(db: Session, household_id, meal_date: date) -> list[AdoptedMealPlan]:
    plans = db.scalars(select(AdoptedMealPlan).where(AdoptedMealPlan.household_id == household_id,
                                                     AdoptedMealPlan.meal_date == meal_date,
                                                     AdoptedMealPlan.status == 'adopted')).all()
    return sorted(plans, key=lambda p: (_MEAL_ORDER.get(p.meal_type, 9), p.adopted_at))


def default_window(until: date | None, days: int) -> tuple[date, date]:
    end = until or date.today()
    return end - timedelta(days=days - 1), end
