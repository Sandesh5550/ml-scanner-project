"""Initial baseline migration for PRAMAAN production database.

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # Preflight check: prevent unsafe upgrade on existing unversioned database
    if "users" in existing_tables:
        raise RuntimeError(
            "\n[ALEMBIC SAFETY PREFLIGHT ERROR] Unversioned database with existing tables detected.\n"
            "Directly running 'alembic upgrade' will fail because baseline migration '001_initial_schema' "
            "attempts to create tables that already exist.\n\n"
            "OPERATOR ACTION REQUIRED:\n"
            "1. Verify that your existing database schema matches baseline revision 001_initial_schema.\n"
            "2. If and only if the schema matches the baseline, stamp the baseline revision manually:\n"
            "       alembic stamp 001_initial_schema\n"
            "3. Then apply pending revisions:\n"
            "       alembic upgrade head\n"
            "Never automatically stamp an unverified database."
        )

    # 1. users
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), server_default='', nullable=False),
        sa.Column('age', sa.Integer(), nullable=True),
        sa.Column('weight', sa.Float(), nullable=True),
        sa.Column('height', sa.Float(), nullable=True),
        sa.Column('daily_calorie_limit', sa.Integer(), server_default='2000', nullable=False),
        sa.Column('diet_type', sa.String(), nullable=True),
        sa.Column('goal_type', sa.String(), nullable=True),
        sa.Column('goal_target_days', sa.Integer(), server_default='30', nullable=True),
        sa.Column('goal_started_at', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )

    # 2. products
    op.create_table(
        'products',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('barcode', sa.String(), nullable=False),
        sa.Column('product_name', sa.String(), nullable=False),
        sa.Column('brand', sa.String(), nullable=True),
        sa.Column('nutriscore', sa.String(), nullable=True),
        sa.Column('ingredients', sa.Text(), nullable=True),
        sa.Column('additives', sa.Text(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('barcode'),
    )

    # 3. nutrition
    op.create_table(
        'nutrition',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('calories', sa.Float(), nullable=True),
        sa.Column('fat', sa.Float(), nullable=True),
        sa.Column('sugar', sa.Float(), nullable=True),
        sa.Column('salt', sa.Float(), nullable=True),
        sa.Column('protein', sa.Float(), nullable=True),
        sa.Column('fiber', sa.Float(), nullable=True),
        sa.Column('carbs', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_nutrition_product_id', 'nutrition', ['product_id'], unique=False)

    # 4. scan_history
    op.create_table(
        'scan_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), server_default='1', nullable=False),
        sa.Column('barcode', sa.String(), nullable=False),
        sa.Column('scan_time', sa.String(), nullable=False),
        sa.Column('result', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scan_history_user_time', 'scan_history', ['user_id', 'scan_time'], unique=False)

    # 5. daily_food_log
    op.create_table(
        'daily_food_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), server_default='1', nullable=False),
        sa.Column('barcode', sa.String(), nullable=False),
        sa.Column('product_name', sa.String(), nullable=False),
        sa.Column('calories', sa.Float(), nullable=True),
        sa.Column('fat', sa.Float(), nullable=True),
        sa.Column('sugar', sa.Float(), nullable=True),
        sa.Column('salt', sa.Float(), nullable=True),
        sa.Column('protein', sa.Float(), nullable=True),
        sa.Column('fiber', sa.Float(), nullable=True),
        sa.Column('carbs', sa.Float(), nullable=True),
        sa.Column('consumed_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_daily_food_log_user_consumed', 'daily_food_log', ['user_id', 'consumed_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_daily_food_log_user_consumed', table_name='daily_food_log')
    op.drop_table('daily_food_log')
    op.drop_index('ix_scan_history_user_time', table_name='scan_history')
    op.drop_table('scan_history')
    op.drop_index('ix_nutrition_product_id', table_name='nutrition')
    op.drop_table('nutrition')
    op.drop_table('products')
    op.drop_table('users')
