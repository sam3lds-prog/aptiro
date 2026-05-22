"""phase 5: job providers — freshness tracking + saved searches

Adds provider tracking columns to jobposting and creates the savedsearch
table. Purely additive — no existing column is touched.

Revision ID: 0007_phase5_job_providers
Revises: 0006_strategy_threshold
Create Date: 2026-05-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_phase5_job_providers"
down_revision = "0006_strategy_threshold"
branch_labels = None
depends_on = None


def _has_column(bind, table, col):
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # fresh deploy already has it from 0001; skip
    return col in {c["name"] for c in insp.get_columns(table)}


def _table_exists(bind, table):
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    ts_t = sa.DateTime(timezone=True) if is_pg else sa.DateTime()

    # ── jobposting additions ─────────────────────────────────────────
    for col, ddl in [
        ("provider_source",  sa.String()),
        ("provider_job_id",  sa.String()),
        ("last_seen_at",     ts_t),
        ("is_stale",         sa.Boolean()),
    ]:
        if not _has_column(bind, "jobposting", col):
            op.add_column("jobposting", sa.Column(col, ddl, nullable=True))

    # Back-fill: existing jobs are not stale
    if not is_pg:
        op.execute(sa.text(
            "UPDATE jobposting SET is_stale = 0 WHERE is_stale IS NULL"))
        op.execute(sa.text(
            "UPDATE jobposting SET provider_source = 'manual_import' "
            "WHERE provider_source IS NULL"))
    else:
        op.execute(sa.text(
            "UPDATE jobposting SET is_stale = false WHERE is_stale IS NULL"))
        op.execute(sa.text(
            "UPDATE jobposting SET provider_source = 'manual_import' "
            "WHERE provider_source IS NULL"))

    # ── savedsearch table ────────────────────────────────────────────
    if not _table_exists(bind, "savedsearch"):
        op.create_table(
            "savedsearch",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False, index=True),
            sa.Column("name", sa.String(), nullable=False, default=""),
            sa.Column("query", sa.String(), nullable=False, default=""),
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("min_salary", sa.Integer(), nullable=True),
            sa.Column("max_salary", sa.Integer(), nullable=True),
            sa.Column("work_mode", sa.String(), nullable=True),
            sa.Column("location_filter", sa.String(), nullable=True),
            sa.Column("frequency", sa.String(), nullable=False, default="manual"),
            sa.Column("last_run_at", ts_t, nullable=True),
            sa.Column("created_at", ts_t, nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "savedsearch" in insp.get_table_names():
        op.drop_table("savedsearch")

    for col in ("is_stale", "last_seen_at", "provider_job_id", "provider_source"):
        if "jobposting" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("jobposting")}
            if col in cols:
                op.drop_column("jobposting", col)
