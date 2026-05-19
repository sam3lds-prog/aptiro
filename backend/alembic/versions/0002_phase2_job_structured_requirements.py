"""phase 2: additive job structured_requirements column

Adds JobPosting.structured_requirements (JSON, defaulted to an empty
object). This is purely additive - the flat `requirements` list is
untouched - so the migration is safe and reversible. A fresh deploy
already gets the column from 0001 (which builds from live metadata);
this migration is for deployments created before Phase 2.

Revision ID: 0002_phase2_job_structured_requirements
Revises: 0001_initial
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_phase2_job_structured_requirements"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _has_column(bind, table, column):
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # 0001 will create it complete; nothing to add
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "jobposting", "structured_requirements"):
        return
    json_type = (sa.dialects.postgresql.JSONB
                 if bind.dialect.name == "postgresql" else sa.JSON)
    op.add_column(
        "jobposting",
        sa.Column("structured_requirements", json_type(),
                  nullable=False, server_default="{}"))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "jobposting" in insp.get_table_names() and \
            "structured_requirements" in {
                c["name"] for c in insp.get_columns("jobposting")}:
        op.drop_column("jobposting", "structured_requirements")
