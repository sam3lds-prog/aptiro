"""phase 7: real notification center tables

Creates two new tables:
  * usernotificationpreference  — per-user opt-in settings (email, SMS,
    in-app, thresholds). One row per owner; safe defaults mean nothing
    is ever sent until the user explicitly enables a channel.
  * inappnotification — real in-app notification center items with
    read/unread state. Cleared by the user; not in the privacy bundle.

Purely additive — two brand-new tables, no change to any existing
table — so it is safe and reversible. A fresh deploy already gets both
tables from init_db (SQLModel.metadata.create_all); this migration is
for deployments created before Phase 7.

Revision ID: 0009_phase7_notifications
Revises: 0008_phase6_public_research
Create Date: 2026-05-27
"""
import sqlalchemy as sa
from alembic import op
from sqlmodel import SQLModel

import app  # noqa: F401  — registers all tables on SQLModel.metadata

revision = "0009_phase7_notifications"
down_revision = "0008_phase6_public_research"
branch_labels = None
depends_on = None

_TABLES = ["usernotificationpreference", "inappnotification"]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = insp.get_table_names()
    for tname in _TABLES:
        if tname not in existing:
            SQLModel.metadata.tables[tname].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = insp.get_table_names()
    # Drop in reverse dependency order
    for tname in reversed(_TABLES):
        if tname in existing:
            SQLModel.metadata.tables[tname].drop(bind=bind)
