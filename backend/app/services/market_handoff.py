"""Short-lived, read-only checkout grants bound to the issuing login session."""
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select

from app.api.errors import ApiError, not_found
from app.core.config import settings
from app.db.models import AdoptedMealPlan, UserSession
from app.services.household import is_member_of
from app.services.meal_results import _prices

TTL_SECONDS = 600


def market_origin():
    value = (settings.sim_market_url or '').rstrip('/')
    url = urlsplit(value)
    local = url.hostname in {'localhost', '127.0.0.1', '[::1]', '::1'}
    if (not url.netloc or url.username or url.password or url.query or url.fragment
            or url.path or url.scheme not in {'http', 'https'} or (url.scheme == 'http' and not local)):
        raise ApiError(503, 'MARKET_UNAVAILABLE', '模擬超市網址尚未設定。')
    return value


def _key():
    key = settings.sim_market_handoff_key or ''
    if len(key.encode()) < 32:
        raise ApiError(503, 'MARKET_UNAVAILABLE', '模擬超市連線尚未設定。')
    return key.encode()


def _signature(encoded, session_hash):
    # The stored login hash never leaves the backend. The grant cannot authenticate
    # any application endpoint and stops working when its issuing session is revoked.
    return hmac.new(_key(), (session_hash + '.' + encoded).encode(), hashlib.sha256).hexdigest()


def _plan(db, plan_id, household_id):
    plan = db.get(AdoptedMealPlan, plan_id)
    if plan is None or plan.household_id != household_id:
        raise not_found('找不到此已採用餐食紀錄。')
    if plan.status != 'adopted':
        raise ApiError(409, 'MARKET_PLAN_UNAVAILABLE', '這份餐單已不再是目前採用的餐單。')
    return plan


def shopping_plan(plan):
    snapshot = plan.snapshot or {}
    preview = snapshot.get('market_shortages')
    source = preview if preview is not None else (_prices(snapshot['result']) if snapshot.get('result') else None)
    if source is None:
        raise ApiError(409, 'MARKET_SNAPSHOT_MISSING', '這份餐單沒有可用的採用缺料快照。')
    # Explicit allowlist: do not export nutrition, questionnaire, budget or household.
    items = [{k: row.get(k) for k in ('food_id', 'name', 'shortage_grams', 'shortage_ml', 'recipe_release_id')}
             for row in source.get('items', [])]
    unknown = [{k: row.get(k) for k in ('food_id', 'name')} for row in source.get('unquantified_items', [])]
    shortages = {'items': items, 'unquantified_items': unknown}
    result = {'id': str(plan.id), 'status': plan.status, 'snapshot_available': True,
              'meal_date': plan.meal_date.isoformat(), 'result_version': plan.result_version,
              'dishes': [{'dish_name': row.get('dish_name', '')} for row in plan.dishes]}
    if preview is not None:
        result['snapshot'] = {'market_shortages': {**shortages, 'recipe_release_id': preview.get('recipe_release_id')}}
    else:
        result['prices'] = shortages
        result['snapshot'] = {'result': {'dishes': [
            {'recipe_release_id': dish.get('recipe_release_id')}
            for dish in snapshot['result'].get('dishes', [])]}}
    return result


def issue(db, auth, household_id, plan_id):
    origin = market_origin()
    _key()
    plan = _plan(db, plan_id, household_id)
    shopping_plan(plan)
    expires = int(time.time()) + TTL_SECONDS
    payload = {'p': str(plan.id), 'u': str(auth.user_id), 'e': expires, 'n': secrets.token_urlsafe(16)}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    token = encoded + '.' + _signature(encoded, auth.session.token_hash)
    return {'checkout_url': origin + '/checkout#handoff=' + token, 'expires_at': expires}


def redeem(db, token):
    _key()
    try:
        encoded, signature = token.split('.')
        if not re.fullmatch('[0-9a-f]{64}', signature):
            raise ValueError('signature')
        payload = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True))
        plan_id, user_id = uuid.UUID(payload['p']), uuid.UUID(payload['u'])
        expiry = payload['e']
        if type(expiry) is not int or not int(time.time()) < expiry <= int(time.time()) + TTL_SECONDS:
            raise ValueError('expired')
        if not isinstance(payload['n'], str) or len(payload['n']) != 22:
            raise ValueError('invalid nonce')
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ApiError(410, 'MARKET_HANDOFF_INVALID', '購物連結已失效，請回主程式重新點選一鍵購入。') from None
    plan = db.get(AdoptedMealPlan, plan_id)
    sessions = db.scalars(select(UserSession).where(
        UserSession.user_id == user_id,
        UserSession.expires_at > datetime.now(timezone.utc),
        UserSession.household_id == (plan.household_id if plan else None),
    ))
    valid = any(hmac.compare_digest(signature, _signature(encoded, row.token_hash)) for row in sessions)
    if not valid or plan is None or not is_member_of(db, user_id, plan.household_id):
        raise ApiError(410, 'MARKET_HANDOFF_INVALID', '購物連結已失效，請回主程式重新點選一鍵購入。')
    _plan(db, plan.id, plan.household_id)
    return {'handoff_id': payload['n'], 'expires_at': expiry, 'plan': shopping_plan(plan)}
