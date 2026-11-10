"""採用／不採用資料庫菜單與已採用紀錄（2026-11-03，參考 t11-t20-demo 的 T16 meal_plans）。

此流程依同一版問卷重新查菜單（T11）並重算營養（T12），只有與畫面上的菜單相同、
營養通過且沒有政策／資料阻擋時才保存。T14 生成結果的採用另外由 meal_plans 處理。

- 不採用：記錄這組食譜，同一版問卷下次查詢改給下一組合格組合（指定菜照樣保留），直到使用者採用。
- 採用：單一交易寫入 adopted_meal_plans（含完整快照），問卷改為 confirmed；同一問卷只採用一次，
  重送同一把 Idempotency-Key 回傳同一筆。已採用的菜會在隔天的資料庫菜單排除（T11 昨日去重）。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError, version_conflict
from app.db.models import AdoptedMealPlan, MealMenuRejection, MealRequest
from app.models.recipe import Recipe
from app.services import meal_history, recipe_retrieval, t12_planning, idempotency
from app.services.meal_cost import free_foods
from app.services import meal_request as request_service

ALL_REJECTED = "all_menus_rejected"


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
    out['rejected_count'] = len(rejected)
    return out


def reject(db: Session, row: MealRequest, *, version: int, recipe_ids: list[str], user_id) -> None:
    if row.version != version:
        raise version_conflict()
    if row.status == 'confirmed':
        raise ApiError(409, 'ALREADY_ADOPTED', '這份問卷已經採用菜單，不能再換。')
    db.add(MealMenuRejection(id=uuid.uuid4(), meal_request_id=row.id, request_version=version,
                             recipe_ids=sorted(set(recipe_ids)), created_by=user_id))
    db.flush()


def adopt(db: Session, *, household_id, user_id, row: MealRequest, draft, members, version: int,
          recipe_ids: list[str], idempotency_key: str) -> tuple[AdoptedMealPlan, bool]:
    """重驗版本與菜單、重算營養後保存；回傳 (紀錄, 是否新建)。"""
    from app.services.meal_plans import _lock_household
    _lock_household(db, household_id)
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
    if preview.get('status') != 'nutrition_passed' or preview.get('adoption_blocked', False):
        raise ApiError(409, 'NOT_ADOPTABLE', '這份菜單的營養沒有通過，不能採用。')
    dishes = [{'standard_dish_id': d['standard_dish_id'], 'dish_name': d['recipe_name'],
               'aliases': d.get('aliases') or [], 'source': 'database', 'recipe_id': d['recipe_id']}
              for d in selected]
    plan = meal_history.record_adopted(db, household_id, draft.meal_date, draft.meal_type,
                                       mode='database_menu', dishes=dishes)
    plan.meal_request_id, plan.request_version = row.id, row.version
    plan.idempotency_key, plan.request_hash, plan.adopted_by = idempotency_key, digest, user_id
    snapshot = _snapshot(selected, preview, draft)
    if draft.mode == 'picker':
        snapshot['market_shortages'] = _market_shortages(db, recipe_ids, preview, draft.available_ingredient_ids)
    plan.snapshot = snapshot
    row.status = 'confirmed'
    db.flush()
    return plan, True


def _snapshot(selected: list[dict], preview: dict, draft) -> dict:
    """畫面需要的完整快照：菜（含用量與步驟）、主食、七種營養素、每人份量與每日建議、目標。"""
    keep = ('status', 'status_message', 'targets', 'nutrition_all', 'dishes', 'staple', 'people',
            'daily_reference_meta', 'dish_portions')
    nutrition = preview.get('nutrition') or {}
    snapshot = {'selected': selected, 'staple_choice': draft.staple,
                'nutrition': {'totals': nutrition.get('totals')}, **{k: preview.get(k) for k in keep}}
    # JSONB 欄位：Decimal／Fraction 以字串保存（精確值，不轉成 float）
    return json.loads(json.dumps(snapshot, ensure_ascii=False, default=str))


def _market_shortages(db: Session, recipe_ids: list[str], preview: dict, available_food_ids: list[str]) -> dict:
    """Freeze the picker’s scaled amounts and T01 identity for simulated shopping."""
    ids = set(recipe_ids)
    releases = db.scalars(select(Recipe.recipe_release_id).where(Recipe.recipe_id.in_(ids))).all()
    release_id = releases[0] if releases and len(releases) == len(ids) and len(set(releases)) == 1 else None
    free = free_foods(available_food_ids)
    ingredients = [line for dish in preview.get('dishes') or [] for line in dish['ingredients']]
    if staple := preview.get('staple'):
        ingredients.append({'food_id': staple['food_id'], 'name': staple['name'], 'grams': staple.get('final_grams')})

    amounts: dict[tuple[str, bool, bool], tuple[Fraction | None, Fraction | None]] = {}
    unknown: set[str] = set()
    names: dict[str, str] = {}
    for line in ingredients:
        food_id = line['food_id']
        if food_id in free:
            continue
        names.setdefault(food_id, line['name'])
        grams = line.get('grams')
        volume = line.get('volume_ml')
        if grams is None and volume is None:
            unknown.add(food_id)
        else:
            key = (food_id, grams is not None, volume is not None)
            prior_g, prior_ml = amounts.get(key, (Fraction(), Fraction()))
            amounts[key] = (prior_g + Fraction(grams) if grams is not None else None,
                            prior_ml + Fraction(volume) if volume is not None else None)

    items = []
    for (food_id, _, _), (grams, volume) in amounts.items():
        if food_id in unknown or not any(value is not None and value > 0 for value in (grams, volume)):
            continue
        item = {'food_id': food_id, 'name': names[food_id],
                'shortage_grams': str(grams) if grams is not None else None}
        if volume is not None:
            item['shortage_ml'] = str(volume)
        items.append(item)

    return {'recipe_release_id': release_id,
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


def trend(db: Session, household_id, since: date, until: date) -> list[dict]:
    """每筆已採用餐食的整餐熱量與蛋白質（成本待 T13 整合後再加）。"""
    plans = db.scalars(select(AdoptedMealPlan).where(AdoptedMealPlan.household_id == household_id,
                                                     AdoptedMealPlan.status == 'adopted',
                                                     AdoptedMealPlan.meal_date >= since,
                                                     AdoptedMealPlan.meal_date <= until))
    out = []
    for p in plans:
        s = summary(p)
        targets = (p.snapshot or {}).get('targets') or {}
        out.append({'meal_date': s['meal_date'], 'meal_type': s['meal_type'], 'id': s['id'],
                    'energy_kcal': _number(s['energy_kcal']), 'protein_g': _number(s['protein_g']),
                    'target_energy_kcal': _number(targets.get('energy_kcal')),
                    'target_protein_g': _number(targets.get('protein_g'))})
    return sorted(out, key=lambda i: (i['meal_date'], _MEAL_ORDER.get(i['meal_type'], 9)))


def default_window(until: date | None, days: int) -> tuple[date, date]:
    end = until or date.today()
    return end - timedelta(days=days - 1), end
