"""Document DB persistence for Agent 3.

Stores and retrieves Section objects from the ``sections`` Postgres table.
Uses the synchronous SQLAlchemy session from common/db.py — Agent 3 has no
LLM calls and no async I/O, so sync is simpler here.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from common.db import SessionLocal
from common.schemas import Section
from common.storage_models import SectionRow


def persist_section(section: Section, report_id: str, db: Session | None = None) -> str:
    """Persist a fully-assembled Section to the Document DB.

    Args:
        section:   A Section produced by Agent 2.
        report_id: The report this section belongs to.
        db:        Optional external session (injected in tests); a new session
                   is opened when None.

    Returns:
        The generated UUID string that uniquely identifies this row — the
        "document key" referenced in VectorIndexerOutput.document_keys.
    """
    row_id = str(uuid.uuid4())
    row = SectionRow(
        id=row_id,
        report_id=report_id,
        section_name_canonical=section.section_name_canonical,
        section_data=section.model_dump(mode="json"),
    )
    _own_session = db is None
    session: Session = SessionLocal() if _own_session else db
    try:
        session.add(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if _own_session:
            session.close()
    return row_id


def get_section(document_key: str, db: Session | None = None) -> Section:
    """Fetch a Section by its document key.

    Args:
        document_key: UUID returned by persist_section().
        db:           Optional external session (injected in tests).

    Returns:
        Section reconstructed from the stored JSONB blob.

    Raises:
        KeyError: if no row with the given id exists.
    """
    _own_session = db is None
    session: Session = SessionLocal() if _own_session else db
    try:
        row: SectionRow | None = session.get(SectionRow, document_key)
        if row is None:
            raise KeyError(f"No section found for document_key='{document_key}'")
        return Section.model_validate(row.section_data)
    finally:
        if _own_session:
            session.close()


def get_sections_by_report(report_id: str, db: Session | None = None) -> list[Section]:
    """Fetch all sections for a report, ordered by insertion time.

    Used by agents that need every section for a report (e.g. Agent 4
    iterating across all canonical sections for a KPI).

    Args:
        report_id: The report whose sections to fetch.
        db:        Optional external session (injected in tests).

    Returns:
        List of Section objects in insertion order (oldest first).
    """
    _own_session = db is None
    session: Session = SessionLocal() if _own_session else db
    try:
        rows: list[SectionRow] = (
            session.query(SectionRow)
            .filter(SectionRow.report_id == report_id)
            .order_by(SectionRow.created_at)
            .all()
        )
        return [Section.model_validate(row.section_data) for row in rows]
    finally:
        if _own_session:
            session.close()
