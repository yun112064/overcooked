"""Add isolated management version/workflow tables; do not import legacy approvals."""
from datetime import datetime, timezone
import sqlalchemy as sa
from alembic import op
from app.management.schema_v1 import define_tables, install_guards

revision = 'm1107management1'
down_revision = 'd1105merge1'
branch_labels = None
depends_on = None

def upgrade():
    connection=op.get_bind()
    metadata=sa.MetaData()
    tables=define_tables(metadata)
    for table in metadata.sorted_tables:
        if table.name.startswith('management_'):table.create(connection,checkfirst=False)
    connection.execute(tables['access_state'].insert().values(id='global',revision=1))
    for channel in ('recipe_catalog','ingredient_catalog','price_policy','source_policy'):
        connection.execute(tables['active_bindings'].insert().values(channel=channel,publication_id=None,revision=1,updated_by=None,updated_at=datetime.now(timezone.utc)))
    install_guards(connection)

def downgrade():
    connection=op.get_bind()
    # Refuse destructive rollback once any evidence, grants or workflow exists.
    for suffix in ('role_grants','artifacts','proposals','publications','commands','schema_adoptions'):
        if connection.scalar(sa.text('SELECT count(*) FROM management_'+suffix)):
            raise RuntimeError('Management data exists; use forward repair or a verified backup')
    metadata=sa.MetaData()
    tables=define_tables(metadata)
    for table in reversed(metadata.sorted_tables):
        if table.name.startswith('management_'):table.drop(connection)
    if connection.dialect.name=='postgresql':
        connection.exec_driver_sql('DROP FUNCTION management_check_member()')
        connection.exec_driver_sql('DROP FUNCTION management_reject_mutation()')
