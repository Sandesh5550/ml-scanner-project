"""Add ocr_jobs table and user allergens column

Revision ID: 002_add_ocr_jobs_and_allergens
Revises: 001_initial_schema
Create Date: 2026-10-20

WARNING: DOWNGRADE IS DESTRUCTIVE.
Downgrading this revision will:
1. Permanently drop table 'ocr_jobs', destroying all OCR job records, cached image hashes, results, and error logs.
2. Permanently drop column 'allergens' from table 'users', destroying user allergen profiles.
DO NOT execute downgrade in production or against real development databases without taking a full backup first.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002_add_ocr_jobs_and_allergens'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # 1. Validate 'users' table and add 'allergens' column
    if 'users' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('users')}
        # Validate critical columns
        required_user_cols = {'id', 'email'}
        missing_user_cols = required_user_cols - existing_cols
        if missing_user_cols:
            raise RuntimeError(
                f"[MIGRATION 002 ERROR] Incompatible 'users' schema: missing critical columns {sorted(missing_user_cols)}. "
                "Automatic repair aborted to prevent data corruption. Manual schema remediation required."
            )

        if 'allergens' not in existing_cols:
            with op.batch_alter_table('users') as batch_op:
                batch_op.add_column(sa.Column('allergens', sa.String(length=255), nullable=True))

    # 2. Validate or create 'ocr_jobs' table
    if 'ocr_jobs' not in existing_tables:
        op.create_table(
            'ocr_jobs',
            sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
            sa.Column('image_hash', sa.String(length=64), nullable=True),
            sa.Column('result_json', sa.Text(), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('started_at', sa.String(length=100), nullable=True),
            sa.Column('completed_at', sa.String(), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_ocr_jobs_user_id', 'ocr_jobs', ['user_id'], unique=False)
        op.create_index('ix_ocr_jobs_status', 'ocr_jobs', ['status'], unique=False)
    else:
        # Pre-existing table: validate schema compatibility before applying alterations
        existing_cols = {c['name'] for c in inspector.get_columns('ocr_jobs')}
        required_ocr_cols = {'id', 'user_id', 'status'}
        missing_ocr_cols = required_ocr_cols - existing_cols
        if missing_ocr_cols:
            raise RuntimeError(
                f"[MIGRATION 002 ERROR] Incompatible 'ocr_jobs' schema: missing critical columns {sorted(missing_ocr_cols)}. "
                "Automatic repair aborted to prevent data corruption. Manual schema remediation required."
            )

        pk_constraint = inspector.get_pk_constraint('ocr_jobs')
        pk_cols = pk_constraint.get('constrained_columns', []) if pk_constraint else []
        if 'id' not in pk_cols:
            raise RuntimeError(
                f"[MIGRATION 002 ERROR] Incompatible 'ocr_jobs' schema: primary key is {pk_cols}; expected ['id']. "
                "Automatic repair aborted. Manual schema remediation required."
            )

        if 'started_at' not in existing_cols:
            with op.batch_alter_table('ocr_jobs') as batch_op:
                batch_op.add_column(sa.Column('started_at', sa.String(length=100), nullable=True))

        existing_indexes = {idx['name'] for idx in inspector.get_indexes('ocr_jobs')}
        if 'ix_ocr_jobs_user_id' not in existing_indexes:
            op.create_index('ix_ocr_jobs_user_id', 'ocr_jobs', ['user_id'], unique=False)
        if 'ix_ocr_jobs_status' not in existing_indexes:
            op.create_index('ix_ocr_jobs_status', 'ocr_jobs', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade revision 002.

    CRITICAL WARNING: This operation permanently destroys data!
    - Drops table 'ocr_jobs' and all stored job history.
    - Drops column 'users.allergens' and all stored allergy profiles.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # 1. Drop ocr_jobs indexes and table if present
    if 'ocr_jobs' in existing_tables:
        existing_indexes = {idx['name'] for idx in inspector.get_indexes('ocr_jobs')}
        if 'ix_ocr_jobs_status' in existing_indexes:
            op.drop_index('ix_ocr_jobs_status', table_name='ocr_jobs')
        if 'ix_ocr_jobs_user_id' in existing_indexes:
            op.drop_index('ix_ocr_jobs_user_id', table_name='ocr_jobs')
        op.drop_table('ocr_jobs')

    # 2. Drop allergens column from users if present
    if 'users' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('users')}
        if 'allergens' in existing_cols:
            with op.batch_alter_table('users') as batch_op:
                batch_op.drop_column('allergens')
