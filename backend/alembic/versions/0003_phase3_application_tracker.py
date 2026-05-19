"""phase 3: application tracker table

Creates the `application` table (the post-submission, human-in-loop
tracker). Purely additive - a brand-new table, no change to any
existing table - so it is safe and reversible. A fresh deploy already
gets the table from 0001 (which builds from live metadata); this
migration is for deployments created before Phase 3.

Revision ID: 0003_phase3_application_tracker
Revises: 0002_phase2_job_structured_requirements
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op
from sqlmodel import SQLModel

import app  # noqa: F401  -> registers all tables on SQLModel.metadata

revision = "0003_phase3_application_tracker"
down_revision = "0002_phase2_job_structured_requirements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "application" in insp.get_table_names():
        return  # already present (e.g. created from live metadata)
    # Create exactly the Application table from the live model metadata
    # so the migration can never drift from the SQLModel definition.
    SQLModel.metadata.tables["application"].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "application" in insp.get_table_names():
        SQLModel.metadata.tables["application"].drop(bind=bind)
