"""phase 6 (upgrade): public research module

Creates the `publicresearchfinding` table. Purely additive — a brand-new
table, no change to any existing table — so it is safe and reversible.
A fresh deploy already gets it from 0001 (built from live metadata);
this migration is for deployments created before Upgrade Phase 6.

Revision ID: 0008_phase6_public_research
Revises: 0007_phase5_job_providers
Create Date: 2026-05-22
"""
import sqlalchemy as sa
from alembic import op
from sqlmodel import SQLModel

import app  # noqa: F401  → registers all tables on SQLModel.metadata

revision = "0008_phase6_public_research"
down_revision = "0007_phase5_job_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "publicresearchfinding" in insp.get_table_names():
        return  # already present (fresh deploy from live metadata)
    SQLModel.metadata.tables["publicresearchfinding"].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "publicresearchfinding" in insp.get_table_names():
        SQLModel.metadata.tables["publicresearchfinding"].drop(bind=bind)
