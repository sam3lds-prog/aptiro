"""phase 4: multi-user & auth

Adds the `user` table and an additive, defaulted `owner_id` column to
the six owner-rooted tables (source, profileclaim, strategy, jobposting,
applicationpackage, application), then backfills every pre-Phase-4 row
to the built-in 'local' user so existing single-user data stays visible
exactly as before. Purely additive and reversible: no drops, no type
changes, no data loss.

A fresh deploy already gets everything from 0001 (which builds from live
metadata); this migration is for deployments created before Phase 4.

Revision ID: 0004_phase4_multiuser_auth
Revises: 0003_phase3_application_tracker
Create Date: 2026-05-19
"""
import sqlalchemy as sa
from alembic import op
from sqlmodel import SQLModel

import app  # noqa: F401  -> registers all tables on SQLModel.metadata

revision = "0004_phase4_multiuser_auth"
down_revision = "0003_phase3_application_tracker"
branch_labels = None
depends_on = None

_OWNED = ("source", "profileclaim", "strategy", "jobposting",
          "applicationpackage", "application")
DEFAULT_UID = "local"


def _cols(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "user" not in tables:
        SQLModel.metadata.tables["user"].create(bind=bind)

    for t in _OWNED:
        if t not in tables:
            continue
        if "owner_id" in _cols(insp, t):
            continue
        op.add_column(t, sa.Column(
            "owner_id", sa.String(), nullable=False,
            server_default=DEFAULT_UID))
        op.create_index("ix_%s_owner_id" % t, t, ["owner_id"])

    # Backfill any pre-existing rows to the built-in local user.
    for t in _OWNED:
        if t in tables and "owner_id" in _cols(sa.inspect(bind), t):
            op.execute(
                "UPDATE %s SET owner_id = '%s' WHERE owner_id IS NULL "
                "OR owner_id = ''" % (t, DEFAULT_UID))

    # Seed the built-in local user row if absent.
    op.execute(
        "INSERT INTO \"user\" (id, email, name, password_hash, token, "
        "is_default, created_at) "
        "SELECT '%s', 'local@aptiro.local', 'Local User', '', '', "
        "%s, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM \"user\" WHERE id = '%s')"
        % (DEFAULT_UID,
           "TRUE" if bind.dialect.name == "postgresql" else "1",
           DEFAULT_UID))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    for t in _OWNED:
        if t in tables and "owner_id" in _cols(insp, t):
            try:
                op.drop_index("ix_%s_owner_id" % t, t)
            except Exception:
                pass
            op.drop_column(t, "owner_id")
    if "user" in tables:
        SQLModel.metadata.tables["user"].drop(bind=bind)
