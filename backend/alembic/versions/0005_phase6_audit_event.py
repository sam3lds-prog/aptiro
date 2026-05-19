"""phase 6: audit event table

Creates the `auditevent` table (append-only observability trail written
by the middleware). Purely additive - a brand-new table, no change to
any existing table - so it is safe and reversible. A fresh deploy
already gets it from 0001 (built from live metadata); this migration is
for deployments created before Phase 6.

Revision ID: 0005_phase6_audit_event
Revises: 0004_phase4_multiuser_auth
Create Date: 2026-05-19
"""
import sqlalchemy as sa
from alembic import op
from sqlmodel import SQLModel

import app  # noqa: F401  -> registers all tables on SQLModel.metadata

revision = "0005_phase6_audit_event"
down_revision = "0004_phase4_multiuser_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "auditevent" in insp.get_table_names():
        return
    SQLModel.metadata.tables["auditevent"].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "auditevent" in insp.get_table_names():
        SQLModel.metadata.tables["auditevent"].drop(bind=bind)
