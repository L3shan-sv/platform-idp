"""Initial schema baseline

Revision ID: 001
Create Date: 2024-06-01 00:00:00.000000

This is the baseline migration generated from the init.sql schema.
It does not execute any SQL — the schema is already in place via
phase-1/infra/docker/postgres/init.sql which runs on container first-start.

New deployments:
  If deploying to a fresh database without the init.sql having run:
    1. Remove the 'pass' statements and uncomment the full schema DDL
    2. Or run init.sql first: psql -f phase-1/infra/docker/postgres/init.sql

Subsequent migrations:
  All schema changes after the initial deployment go here as new revision files.
  Example: add column, create index, alter type.
  Never modify this file — it is the immutable baseline.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Baseline migration — schema already applied via init.sql
    # Stamping this revision marks the database as at version 001
    # without executing any DDL.
    pass


def downgrade() -> None:
    # Cannot downgrade from baseline
    raise NotImplementedError("Cannot downgrade below baseline migration 001")
