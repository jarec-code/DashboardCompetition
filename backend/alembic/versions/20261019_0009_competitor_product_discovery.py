"""competitor product discovery

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19 12:30:44.431606
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('discovery_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('competitor_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('phase', sa.String(length=16), nullable=False),
    sa.Column('source_type', sa.String(length=16), nullable=True),
    sa.Column('queue', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('done_urls', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('seen_keys', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('requests', sa.Integer(), nullable=False),
    sa.Column('errors_in_row', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('discovery', 'prices')", name=op.f('ck_discovery_runs_kind')),
    sa.CheckConstraint("phase IN ('detect', 'list', 'enrich', 'done')", name=op.f('ck_discovery_runs_phase')),
    sa.CheckConstraint("status IN ('running', 'succeeded', 'failed', 'stopped')", name=op.f('ck_discovery_runs_status')),
    sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], name=op.f('fk_discovery_runs_competitor_id_competitors'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_discovery_runs'))
    )
    op.create_index(op.f('ix_discovery_runs_competitor_id'), 'discovery_runs', ['competitor_id'], unique=False)
    op.create_index('ix_discovery_runs_open', 'discovery_runs', ['status', 'competitor_id'], unique=False)
    op.create_table('competitor_catalog_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('competitor_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=2048), nullable=False),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=True),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('price_from_listing', sa.Boolean(), nullable=False),
    sa.Column('identifiers', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('hints', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('match_status', sa.String(length=16), nullable=False),
    sa.Column('match_method', sa.String(length=16), nullable=True),
    sa.Column('product_id', sa.Integer(), nullable=True),
    sa.Column('offer_id', sa.Integer(), nullable=True),
    sa.Column('candidates', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('enriched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('enrich_error', sa.Text(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('missing_runs', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("match_method IN ('identifier', 'attributes', 'manual', 'existing')", name=op.f('ck_catalog_items_method')),
    sa.CheckConstraint("match_status IN ('new', 'matched', 'review', 'unrelated', 'ignored')", name=op.f('ck_catalog_items_match')),
    sa.CheckConstraint("status IN ('active', 'gone')", name=op.f('ck_catalog_items_status')),
    sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], name=op.f('fk_competitor_catalog_items_competitor_id_competitors'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['offer_id'], ['competitor_products.id'], name=op.f('fk_competitor_catalog_items_offer_id_competitor_products'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_competitor_catalog_items_product_id_products'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_competitor_catalog_items')),
    sa.UniqueConstraint('competitor_id', 'key', name='uq_catalog_items_key')
    )
    op.create_index('ix_catalog_items_competitor_match', 'competitor_catalog_items', ['competitor_id', 'match_status'], unique=False)
    op.create_index(op.f('ix_competitor_catalog_items_competitor_id'), 'competitor_catalog_items', ['competitor_id'], unique=False)
    op.add_column('competitor_products', sa.Column('price_source', sa.String(length=16), server_default='page', nullable=False))
    op.add_column('scrape_configs', sa.Column('discovery', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.create_check_constraint(op.f('ck_competitor_products_price_source'), 'competitor_products',
                               "price_source IN ('page', 'listing')")
    op.drop_constraint(op.f('ck_competitor_products_matching_method'), 'competitor_products', type_='check')
    op.create_check_constraint(op.f('ck_competitor_products_matching_method'), 'competitor_products',
                               "matching_method IN ('identifier', 'name', 'manual', 'ai', 'attributes')")


def downgrade() -> None:
    op.execute("UPDATE competitor_products SET matching_method = 'manual' WHERE matching_method = 'attributes'")
    op.drop_constraint(op.f('ck_competitor_products_matching_method'), 'competitor_products', type_='check')
    op.create_check_constraint(op.f('ck_competitor_products_matching_method'), 'competitor_products',
                               "matching_method IN ('identifier', 'name', 'manual', 'ai')")
    op.drop_constraint(op.f('ck_competitor_products_price_source'), 'competitor_products', type_='check')
    op.drop_column('scrape_configs', 'discovery')
    op.drop_column('competitor_products', 'price_source')
    op.drop_index(op.f('ix_competitor_catalog_items_competitor_id'), table_name='competitor_catalog_items')
    op.drop_index('ix_catalog_items_competitor_match', table_name='competitor_catalog_items')
    op.drop_table('competitor_catalog_items')
    op.drop_index('ix_discovery_runs_open', table_name='discovery_runs')
    op.drop_index(op.f('ix_discovery_runs_competitor_id'), table_name='discovery_runs')
    op.drop_table('discovery_runs')
