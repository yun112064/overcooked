"""T16 adoption transaction: extend adopted_meal_plans with versions, idempotency and full snapshot

以 ALTER 擴充 T11 的 adopted_meal_plans（不另建平行表）：
- meal_request_id／generation_result_id／result_version／request_version：採用的問卷與結果版本。
  問卷或結果之後被刪除時改為 NULL，已採用歷史與快照保留。
- idempotency_key＋request_hash：(household_id, idempotency_key) 唯一；同一問卷只採用一次（meal_request_id 唯一）。
- snapshot：MealPlanSnapshot（配方、參與項目、目標、來源／規則版本、缺值標示、知識引用、昨日比對）。
- price_exception_accepted：當次是否以缺價例外採用。adopted_by：採用者。
T11 寫入的最小紀錄這些欄位維持 NULL／預設值。

Revision ID: d9a4b7e2c5f1
Revises: c8e3f6a1d2b4
Create Date: 2026-10-20 23:00:00
"""
from typing import Sequence, Union
import hashlib

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d9a4b7e2c5f1"
down_revision: Union[str, Sequence[str], None] = "c8e3f6a1d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "adopted_meal_plans"


def upgrade() -> None:
    if 'meal_request_id' not in _columns(TABLE):
        op.add_column(TABLE, sa.Column('meal_request_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('meal_requests.id', ondelete='SET NULL', name='fk_adopted_meal_plans_meal_request'), nullable=True))
    if 'generation_result_id' not in _columns(TABLE):
        op.add_column(TABLE, sa.Column('generation_result_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('generation_results.id', ondelete='SET NULL', name='fk_adopted_meal_plans_generation_result'), nullable=True))
    if 'result_version' not in _columns(TABLE):
        op.add_column(TABLE, sa.Column('result_version', sa.Integer(), nullable=True))
    if 'request_version' not in _columns(TABLE):
        op.add_column(TABLE, sa.Column('request_version', sa.Integer(), nullable=True))
    if 'idempotency_key' not in _columns(TABLE):
        op.add_column(TABLE, sa.Column('idempotency_key', sa.String(100), nullable=True))
    if 'request_hash' not in _columns(TABLE):
        op.add_column(TABLE, sa.Column('request_hash', sa.String(64), nullable=True))
    if 'price_exception_accepted' not in _columns(TABLE):
        op.add_column(TABLE, sa.Column('price_exception_accepted', sa.Boolean(), nullable=False, server_default=sa.false()))
    if 'snapshot' not in _columns(TABLE):
        op.add_column(TABLE, sa.Column('snapshot', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    if 'adopted_by' not in _columns(TABLE):
        op.add_column(TABLE, sa.Column('adopted_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL', name='fk_adopted_meal_plans_adopted_by'), nullable=True))
    if 'uq_adopted_meal_plan_idempotency' not in _constraints(TABLE):
        op.create_unique_constraint('uq_adopted_meal_plan_idempotency', TABLE, ['household_id', 'idempotency_key'])
    if 'uq_adopted_meal_plan_request' not in _constraints(TABLE):
        op.create_unique_constraint('uq_adopted_meal_plan_request', TABLE, ['meal_request_id'])
    for row in op.get_bind().execute(sa.text(
            "SELECT id, meal_request_id, request_version, snapshot FROM adopted_meal_plans "
            "WHERE idempotency_key IS NOT NULL AND request_hash IS NULL")).mappings():
        ids = sorted({d['recipe_id'] for d in (row['snapshot'] or {}).get('selected', [])})
        parts = ('adopt-preview', row['meal_request_id'], row['request_version'], ids)
        digest = hashlib.sha256(':'.join('-' if p is None else str(p) for p in parts).encode()).hexdigest()
        op.get_bind().execute(sa.text('UPDATE adopted_meal_plans SET request_hash=:digest WHERE id=:id'),
                              {'id': row['id'], 'digest': digest})
    if 'ck_adopted_meal_idempotency_pair' not in _constraints(TABLE):
        op.create_check_constraint('ck_adopted_meal_idempotency_pair', TABLE, '(idempotency_key IS NULL) = (request_hash IS NULL)')


def downgrade() -> None:
    if 'ck_adopted_meal_idempotency_pair' in _constraints(TABLE):
        op.drop_constraint('ck_adopted_meal_idempotency_pair', TABLE, type_='check')
    keep_shared = sa.inspect(op.get_bind()).has_table('meal_menu_rejections')
    if not keep_shared and 'uq_adopted_meal_plan_request' in _constraints(TABLE):
        op.drop_constraint('uq_adopted_meal_plan_request', TABLE, type_='unique')
    if not keep_shared and 'uq_adopted_meal_plan_idempotency' in _constraints(TABLE):
        op.drop_constraint('uq_adopted_meal_plan_idempotency', TABLE, type_='unique')
    for column in ('adopted_by', 'snapshot', 'price_exception_accepted', 'request_hash', 'idempotency_key', 'request_version', 'result_version', 'generation_result_id', 'meal_request_id'):
        if column in _columns(TABLE) and (not keep_shared or column in ('price_exception_accepted', 'request_hash', 'result_version', 'generation_result_id')):
            op.drop_column(TABLE, column)


def _columns(table):
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _constraints(table):
    inspector = sa.inspect(op.get_bind())
    return {c['name'] for c in inspector.get_unique_constraints(table) + inspector.get_check_constraints(table)}
