"""initial schema (Trust + Export slice)

Creates the full Source -> ProfileClaim -> SourceRef spine plus
Strategy, JobPosting, ApplicationPackage/PackageBullet, AgentRun/
AgentCritique, ApplySession, NotificationPreview - exactly as defined
on SQLModel.metadata in app.py - and enables the pgvector extension so
later phases (semantic scoring) can add vector columns without a
schema-tooling change.

Scaffolding rationale: this slice's job is Trust + Export, not schema
migration tooling. Building tables straight from the live SQLModel
metadata guarantees the migration can never drift from the models;
fine-grained autogenerate diffs are a later-phase concern.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-18
"""
from alembic import op
from sqlmodel import SQLModel

import app  # noqa: F401  -> registers all tables on SQLModel.metadata

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    SQLModel.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind=bind)
