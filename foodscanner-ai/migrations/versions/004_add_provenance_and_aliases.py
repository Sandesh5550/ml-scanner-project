"""Add provenance, completeness, category, and canonical_id to products, sodium to nutrition, and create product_barcode_aliases table

Revision ID: 004_add_provenance_and_aliases
Revises: 003_add_saturated_and_trans_fat
Create Date: 2026-11-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '004_add_provenance_and_aliases'
down_revision: Union[str, None] = '003_add_saturated_and_trans_fat'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # 1. Alter 'products' table to add provenance, completeness, and metadata columns
    if 'products' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('products')}
        required_cols = {'id', 'barcode', 'product_name'}
        missing_cols = required_cols - existing_cols
        if missing_cols:
            raise RuntimeError(
                f"[MIGRATION 004 ERROR] Incompatible 'products' schema: missing critical columns {sorted(missing_cols)}. "
                "Automatic repair aborted to prevent data corruption. Manual schema remediation required."
            )

        with op.batch_alter_table('products') as batch_op:
            if 'category' not in existing_cols:
                batch_op.add_column(sa.Column('category', sa.String(length=255), nullable=True))
            if 'canonical_id' not in existing_cols:
                batch_op.add_column(sa.Column('canonical_id', sa.String(length=64), nullable=True))
            if 'data_source' not in existing_cols:
                batch_op.add_column(sa.Column('data_source', sa.String(length=50), server_default='local_db', nullable=True))
            if 'data_confidence' not in existing_cols:
                batch_op.add_column(sa.Column('data_confidence', sa.String(length=20), server_default='UNVERIFIED', nullable=True))
            if 'nutrition_completeness' not in existing_cols:
                batch_op.add_column(sa.Column('nutrition_completeness', sa.Float(), nullable=True))
            if 'ingredients_completeness' not in existing_cols:
                batch_op.add_column(sa.Column('ingredients_completeness', sa.Float(), nullable=True))
            if 'last_fetched_at' not in existing_cols:
                batch_op.add_column(sa.Column('last_fetched_at', sa.String(length=50), nullable=True))
            if 'last_updated_at' not in existing_cols:
                batch_op.add_column(sa.Column('last_updated_at', sa.String(length=50), nullable=True))
            if 'field_provenance_json' not in existing_cols:
                batch_op.add_column(sa.Column('field_provenance_json', sa.Text(), nullable=True))

        # Create index on canonical_id if not present
        existing_product_indexes = {idx['name'] for idx in inspector.get_indexes('products')}
        if 'ix_products_canonical_id' not in existing_product_indexes:
            op.create_index('ix_products_canonical_id', 'products', ['canonical_id'], unique=False)

    # 2. Alter 'nutrition' table to add 'sodium' column (in grams)
    if 'nutrition' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('nutrition')}
        required_cols = {'id', 'product_id'}
        missing_cols = required_cols - existing_cols
        if missing_cols:
            raise RuntimeError(
                f"[MIGRATION 004 ERROR] Incompatible 'nutrition' schema: missing critical columns {sorted(missing_cols)}. "
                "Automatic repair aborted to prevent data corruption. Manual schema remediation required."
            )

        with op.batch_alter_table('nutrition') as batch_op:
            if 'sodium' not in existing_cols:
                batch_op.add_column(sa.Column('sodium', sa.Float(), nullable=True))

    # 3. Create 'product_barcode_aliases' table
    if 'product_barcode_aliases' not in existing_tables:
        op.create_table(
            'product_barcode_aliases',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('product_id', sa.Integer(), nullable=False),
            sa.Column('alias_barcode', sa.String(length=64), nullable=False),
            sa.Column('alias_type', sa.String(length=50), server_default='alternate', nullable=True),
            sa.Column('created_at', sa.String(length=50), nullable=False),
            sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('alias_barcode'),
        )
        op.create_index('ix_product_barcode_aliases_product_id', 'product_barcode_aliases', ['product_id'], unique=False)
        op.create_index('ix_product_barcode_aliases_barcode', 'product_barcode_aliases', ['alias_barcode'], unique=True)

    # 4. Idempotent Data Backfill for existing records
    if 'products' in existing_tables:
        # Populate canonical_id with barcode where not yet set
        conn.execute(
            sa.text(
                "UPDATE products SET canonical_id = barcode "
                "WHERE canonical_id IS NULL OR canonical_id = ''"
            )
        )
        # Ensure default provenance metadata is set for historical records
        conn.execute(
            sa.text(
                "UPDATE products SET data_source = 'local_db' "
                "WHERE data_source IS NULL OR data_source = ''"
            )
        )
        conn.execute(
            sa.text(
                "UPDATE products SET data_confidence = 'UNVERIFIED' "
                "WHERE data_confidence IS NULL OR data_confidence = ''"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # 1. Drop 'product_barcode_aliases' table
    if 'product_barcode_aliases' in existing_tables:
        op.drop_table('product_barcode_aliases')

    # 2. Drop 'sodium' column from 'nutrition'
    if 'nutrition' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('nutrition')}
        with op.batch_alter_table('nutrition') as batch_op:
            if 'sodium' in existing_cols:
                batch_op.drop_column('sodium')

    # 3. Drop columns from 'products'
    if 'products' in existing_tables:
        existing_indexes = {idx['name'] for idx in inspector.get_indexes('products')}
        if 'ix_products_canonical_id' in existing_indexes:
            op.drop_index('ix_products_canonical_id', table_name='products')

        existing_cols = {c['name'] for c in inspector.get_columns('products')}
        cols_to_drop = [
            'field_provenance_json',
            'last_updated_at',
            'last_fetched_at',
            'ingredients_completeness',
            'nutrition_completeness',
            'data_confidence',
            'data_source',
            'canonical_id',
            'category',
        ]
        with op.batch_alter_table('products') as batch_op:
            for col in cols_to_drop:
                if col in existing_cols:
                    batch_op.drop_column(col)
