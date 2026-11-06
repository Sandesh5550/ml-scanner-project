"""Add saturated_fat and trans_fat columns to nutrition and daily_food_log tables

Revision ID: 003_add_saturated_and_trans_fat
Revises: 002_add_ocr_jobs_and_allergens
Create Date: 2026-11-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '003_add_saturated_and_trans_fat'
down_revision: Union[str, None] = '002_add_ocr_jobs_and_allergens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # 1. Add saturated_fat and trans_fat to 'nutrition' table
    if 'nutrition' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('nutrition')}
        required_cols = {'id', 'product_id'}
        missing_cols = required_cols - existing_cols
        if missing_cols:
            raise RuntimeError(
                f"[MIGRATION 003 ERROR] Incompatible 'nutrition' schema: missing critical columns {sorted(missing_cols)}. "
                "Automatic repair aborted to prevent data corruption. Manual schema remediation required."
            )

        with op.batch_alter_table('nutrition') as batch_op:
            if 'saturated_fat' not in existing_cols:
                batch_op.add_column(sa.Column('saturated_fat', sa.Float(), nullable=True))
            if 'trans_fat' not in existing_cols:
                batch_op.add_column(sa.Column('trans_fat', sa.Float(), nullable=True))

    # 2. Add saturated_fat and trans_fat to 'daily_food_log' table
    if 'daily_food_log' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('daily_food_log')}
        required_cols = {'id', 'user_id', 'barcode', 'consumed_at'}
        missing_cols = required_cols - existing_cols
        if missing_cols:
            raise RuntimeError(
                f"[MIGRATION 003 ERROR] Incompatible 'daily_food_log' schema: missing critical columns {sorted(missing_cols)}. "
                "Automatic repair aborted to prevent data corruption. Manual schema remediation required."
            )

        with op.batch_alter_table('daily_food_log') as batch_op:
            if 'saturated_fat' not in existing_cols:
                batch_op.add_column(sa.Column('saturated_fat', sa.Float(), nullable=True))
            if 'trans_fat' not in existing_cols:
                batch_op.add_column(sa.Column('trans_fat', sa.Float(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # 1. Drop columns from 'daily_food_log'
    if 'daily_food_log' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('daily_food_log')}
        with op.batch_alter_table('daily_food_log') as batch_op:
            if 'trans_fat' in existing_cols:
                batch_op.drop_column('trans_fat')
            if 'saturated_fat' in existing_cols:
                batch_op.drop_column('saturated_fat')

    # 2. Drop columns from 'nutrition'
    if 'nutrition' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('nutrition')}
        with op.batch_alter_table('nutrition') as batch_op:
            if 'trans_fat' in existing_cols:
                batch_op.drop_column('trans_fat')
            if 'saturated_fat' in existing_cols:
                batch_op.drop_column('saturated_fat')
