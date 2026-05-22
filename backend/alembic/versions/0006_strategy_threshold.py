"""phase 4: multi-strategy + score threshold

Adds the new `score_threshold` column to the existing `strategy`
table. The Strategy model already has `is_active`, so multi-strategy
support is otherwise purely additional rows — no schema change for
that. This migration is the only thing the Postgres path needs.

- A fresh deploy already gets the column from `0001` (which builds
  from live SQLModel metadata) once the model has the new field.
- An existing Postgres deploy that was created before Phase 4 gets
  the column added by THIS migration.
- An existing SQLite deploy is healed at boot by the additive-column
  scan in `app.py::_ensure_additive_columns`.

All three paths converge on the same schema. Purely additive: no
data is moved, no column is dropped, no type changes.

Revision ID: 0004_phase4_multi_strategy
Revises: 0003_phase3_application_tracker
Create Date: 2026-05-20
"""
import sqlalchemy as sa
from alembic import op

import app  # noqa: F401  -> registers all tables on SQLModel.metadata

revision = "0006_strategy_threshold"
down_revision = "0005_phase6_audit_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "strategy" not in insp.get_table_names():
        # The fresh-deploy path already built the table from live
        # metadata, including the new column. Nothing to do.
        return
    cols = {c["name"] for c in insp.get_columns("strategy")}
    if "score_threshold" not in cols:
        with op.batch_alter_table("strategy") as batch:
            batch.add_column(sa.Column(
                "score_threshold",
                sa.Integer(),
                nullable=False,
                server_default="0"))
        # Drop the server_default after backfill so the model is the
        # source of truth going forward (the SQLModel field default
        # of 0 takes over for new rows).
        with op.batch_alter_table("strategy") as batch:
            batch.alter_column("score_threshold", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "strategy" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("strategy")}
    if "score_threshold" in cols:
        with op.batch_alter_table("strategy") as batch:
            batch.drop_column("score_threshold")
