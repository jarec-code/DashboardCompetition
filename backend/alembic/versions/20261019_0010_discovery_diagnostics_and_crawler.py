"""discovery diagnostics and crawler

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19 15:49:12.816670
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('discovery_urls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('competitor_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('template', sa.String(length=600), nullable=False),
    sa.Column('depth', sa.Integer(), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('links_found', sa.Integer(), nullable=False),
    sa.Column('products_found', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('unknown', 'product', 'listing', 'category', 'info', 'skip')", name=op.f('ck_discovery_urls_kind')),
    sa.CheckConstraint("status IN ('queued', 'done', 'failed', 'skipped')", name=op.f('ck_discovery_urls_status')),
    sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], name=op.f('fk_discovery_urls_competitor_id_competitors'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['discovery_runs.id'], name=op.f('fk_discovery_urls_run_id_discovery_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_discovery_urls')),
    sa.UniqueConstraint('competitor_id', 'url', name='uq_discovery_urls_url')
    )
    op.create_index('ix_discovery_urls_queue', 'discovery_urls', ['competitor_id', 'status', 'priority'], unique=False)
    op.add_column('competitor_catalog_items', sa.Column('found_by', sa.String(length=16), nullable=True))
    op.add_column('competitor_catalog_items', sa.Column('parsed_by', sa.String(length=32), nullable=True))
    op.add_column('competitor_catalog_items', sa.Column('fetch_status', sa.String(length=16), nullable=True))
    op.add_column('competitor_catalog_items', sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('competitor_catalog_items', sa.Column('canonical_url', sa.String(length=2048), nullable=True))
    op.add_column('competitor_catalog_items', sa.Column('category', sa.String(length=300), nullable=True))
    op.add_column('competitor_catalog_items', sa.Column('completeness', sa.Integer(), nullable=True))


    # CHECK constraints are not detected by autogenerate
    op.create_check_constraint(op.f('ck_catalog_items_found_by'), 'competitor_catalog_items',
                               "found_by IN ('sitemap', 'feed', 'listing', 'adapter', 'crawl')")
    op.create_check_constraint(op.f('ck_catalog_items_fetch_status'), 'competitor_catalog_items',
                               "fetch_status IN ('ok', 'no_price', 'not_product', 'failed', 'skipped')")
    op.drop_constraint(op.f('ck_discovery_runs_status'), 'discovery_runs', type_='check')
    op.create_check_constraint(op.f('ck_discovery_runs_status'), 'discovery_runs',
                               "status IN ('running', 'succeeded', 'partial', 'failed', 'stopped')")
    # Items read before this migration: source and parser as they were (adapter/sitemap runs, parser unknown).
    op.execute("UPDATE competitor_catalog_items SET found_by = 'adapter' WHERE key LIKE 'polozka:%'")
    op.execute("UPDATE competitor_catalog_items SET fetch_status = CASE WHEN enrich_error IS NOT NULL "
               "THEN 'not_product' ELSE 'ok' END, fetched_at = enriched_at WHERE enriched_at IS NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE discovery_runs SET status = 'succeeded' WHERE status = 'partial'")
    op.drop_constraint(op.f('ck_discovery_runs_status'), 'discovery_runs', type_='check')
    op.create_check_constraint(op.f('ck_discovery_runs_status'), 'discovery_runs',
                               "status IN ('running', 'succeeded', 'failed', 'stopped')")
    op.drop_constraint(op.f('ck_catalog_items_fetch_status'), 'competitor_catalog_items', type_='check')
    op.drop_constraint(op.f('ck_catalog_items_found_by'), 'competitor_catalog_items', type_='check')
    op.drop_column('competitor_catalog_items', 'completeness')
    op.drop_column('competitor_catalog_items', 'category')
    op.drop_column('competitor_catalog_items', 'canonical_url')
    op.drop_column('competitor_catalog_items', 'fetched_at')
    op.drop_column('competitor_catalog_items', 'fetch_status')
    op.drop_column('competitor_catalog_items', 'parsed_by')
    op.drop_column('competitor_catalog_items', 'found_by')
    op.drop_index('ix_discovery_urls_queue', table_name='discovery_urls')
    op.drop_table('discovery_urls')
