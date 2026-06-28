"""Enable pgvector extension and create the sections table.

Revision ID: 0001
Revises:
Create Date: 2026-06-27

Agent attribution: Agent 3 (Persistence & Indexing).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector — idempotent, safe to run multiple times.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "sections",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("report_id", sa.String(), nullable=False),
        sa.Column("section_name_canonical", sa.String(), nullable=False),
        sa.Column("section_data", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sections_report_id", "sections", ["report_id"])
    op.create_index("idx_sections_section_name_canonical", "sections", ["section_name_canonical"])
    op.create_index(
        "idx_sections_report_canonical",
        "sections",
        ["report_id", "section_name_canonical"],
    )


def downgrade() -> None:
    op.drop_index("idx_sections_report_canonical", table_name="sections")
    op.drop_index("idx_sections_section_name_canonical", table_name="sections")
    op.drop_index("idx_sections_report_id", table_name="sections")
    op.drop_table("sections")
    # Do NOT drop the vector extension on downgrade — other tables may use it.
