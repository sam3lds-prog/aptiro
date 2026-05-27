"""phase 8: auth hardening — ExportToken table + token_expires_at on user

Creates the `exporttoken` table and adds `token_expires_at` to the `user`
table. Purely additive — no existing table or column is modified — so it is
safe and reversible. A fresh deploy already gets both from `0001` (which
builds from live SQLModel metadata); this migration is for deployments
created before Upgrade Phase 8.

Revision ID: 0010_phase8_auth_hardening
Revises: 0009_phase7_notifications
Create Date: 2026-05-27
"""

import sqlalchemy as sa
from alembic import op
from sqlmodel import SQLModel

import app  # noqa: F401 — registers all tables on SQLModel.metadata

revision = "0010_phase8_auth_hardening"
down_revision = "0009_phase7_notifications"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True  # table doesn't exist yet; 0001 will create it complete
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. Create exporttoken table if it doesn't exist yet
    if "exporttoken" not in insp.get_table_names():
        SQLModel.metadata.tables["exporttoken"].create(bind=bind)

    # 2. Add token_expires_at to user table
    if not _has_column(bind, "user", "token_expires_at"):
        op.add_column(
            "user",
            sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Remove token_expires_at from user
    if "user" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("user")}
        if "token_expires_at" in cols:
            op.drop_column("user", "token_expires_at")

    # Drop exporttoken table
    if "exporttoken" in insp.get_table_names():
        SQLModel.metadata.tables["exporttoken"].drop(bind=bind)
