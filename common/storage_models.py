"""SQLAlchemy ORM models for the shared Postgres schema.

These describe *storage* shape, not the pipeline wire format — see
common/schemas.py for the Pydantic contracts.  Keep this file additive:
each new agent appends its tables here rather than defining them inline in
an agent-specific module.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.db import Base


class SectionRow(Base):
    """One row per Section persisted by Agent 3.

    The full Section object (including nested tables/charts/footnotes) is
    stored as a single JSONB blob — downstream agents consume it as a Python
    object via Pydantic, not via SQL joins, so normalising it into child tables
    would add schema complexity with no query benefit.

    (report_id, section_name_canonical) is indexed for lookup but intentionally
    NOT a uniqueness constraint — a report may legitimately have multiple
    sections that align to the same canonical name (e.g. two "OTHER" sections,
    or a repeated statement).
    """

    __tablename__ = "sections"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    report_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    section_name_canonical: Mapped[str] = mapped_column(String, nullable=False, index=True)
    section_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index("idx_sections_report_canonical", "report_id", "section_name_canonical"),
    )
