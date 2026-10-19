"""product url, image, availability and data source

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('products', sa.Column('url', sa.String(length=2048), nullable=True))
    op.add_column('products', sa.Column('image_url', sa.String(length=2048), nullable=True))
    op.add_column('products', sa.Column('availability', sa.String(length=16), server_default='unknown',
                                        nullable=False))
    op.add_column('products', sa.Column('source_kind', sa.String(length=16), nullable=True))
    op.add_column('products', sa.Column('source', sa.String(length=255), nullable=True))
    op.add_column('products', sa.Column('source_synced_at', sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint(op.f('uq_products_url'), 'products', ['url'])
    op.create_check_constraint(op.f('ck_products_availability'), 'products',
                               "availability IN ('in_stock', 'out_of_stock', 'preorder', 'unknown')")
    op.create_check_constraint(op.f('ck_products_source_kind'), 'products',
                               "source_kind IN ('file', 'feed')")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_products_source_kind'), 'products', type_='check')
    op.drop_constraint(op.f('ck_products_availability'), 'products', type_='check')
    op.drop_constraint(op.f('uq_products_url'), 'products', type_='unique')
    for column in ('source_synced_at', 'source', 'source_kind', 'availability', 'image_url', 'url'):
        op.drop_column('products', column)
