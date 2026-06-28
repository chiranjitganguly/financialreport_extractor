"""Integration tests for vector_indexer.documents.

These tests hit a real Postgres instance (kpi_extraction_test database).
The docker-compose postgres service must be reachable, and the sections table
must exist (run `alembic upgrade head` against the test DB before running).

To run only these tests:
    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extraction_test \
    pytest tests/test_documents.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from common.schemas import (
    ChartElement,
    FootnoteAnchor,
    FootnoteElement,
    Section,
    TableCell,
    TableElement,
    TableRow,
)
from common.storage_models import Base, SectionRow
from vector_indexer.documents import get_section, get_sections_by_report, persist_section

# ---------------------------------------------------------------------------
# Test DB configuration
# ---------------------------------------------------------------------------

_TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/kpi_extraction_test",
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Session fixture — real Postgres, outer-transaction rollback for isolation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def db_session():
    """Provide a SQLAlchemy session against the test database.

    Uses the outer-transaction / SAVEPOINT pattern so session.commit() inside
    the function under test releases the SAVEPOINT rather than committing to
    Postgres.  The outer transaction is rolled back in teardown, leaving the
    test DB clean regardless of what the test does.

    Requires the test database and sections table to already exist.
    Run ``alembic upgrade head`` against TEST_DATABASE_URL before the suite.
    """
    engine = create_engine(_TEST_DB_URL, pool_pre_ping=True)
    # Ensure the table exists (safe for CI — uses CREATE IF NOT EXISTS semantics)
    Base.metadata.create_all(engine, checkfirst=True)

    conn = engine.connect()
    outer_txn = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer_txn.rollback()
        conn.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_section(
    canonical: str = "Balance Sheet",
    raw: str = "Statement of Financial Position",
    report_id: str = "rpt-test-001",
) -> Section:
    return Section(
        section_name_raw=raw,
        section_name_canonical=canonical,
        alignment_confidence=0.95,
        alignment_source="fuzzy_match",
        content_markdown="Assets and liabilities summary.",
        tables=[],
        charts=[],
        footnotes=[],
        page_range=(5, 8),
    )


def _make_rich_section() -> Section:
    """Section with one table, one chart, one footnote — exercises full round-trip."""
    return Section(
        section_name_raw="Income Statement",
        section_name_canonical="Statement of Profit and Loss",
        alignment_confidence=0.92,
        alignment_source="fuzzy_match",
        content_markdown="Revenue grew 10% year-on-year.",
        tables=[
            TableElement(
                table_id="tbl-rt-001",
                caption="Consolidated P&L",
                section_name_canonical="Statement of Profit and Loss",
                page=3,
                rows=[
                    TableRow(
                        row_label="Revenue",
                        cells=[
                            TableCell(column_label="FY2025", value=1000, footnote_refs=[]),
                            TableCell(column_label="FY2024", value=900, footnote_refs=["fn-rt-001"]),
                        ],
                    )
                ],
                footnote_refs=["fn-rt-001"],
            )
        ],
        charts=[
            ChartElement(
                chart_id="chart-rt-001",
                section_name_canonical="Statement of Profit and Loss",
                page=4,
                image_ref="images/chart001.png",
                interpretation="Revenue shows consistent upward trend.",
                interpretation_confidence=0.88,
                footnote_refs=[],
            )
        ],
        footnotes=[
            FootnoteElement(
                footnote_id="fn-rt-001",
                marker="1",
                text="FY2024 restated for discontinued operations.",
                section_name_canonical="Statement of Profit and Loss",
                page=3,
                anchors=[
                    FootnoteAnchor(element_type="table_cell", element_id="tbl-rt-001", location="cell(0,1)")
                ],
            )
        ],
        page_range=(3, 5),
    )


# ---------------------------------------------------------------------------
# persist_section tests
# ---------------------------------------------------------------------------

class TestPersistSection:
    def test_returns_uuid_string(self, db_session):
        key = persist_section(_make_minimal_section(), report_id="rpt-001", db=db_session)
        assert isinstance(key, str)
        assert len(key) == 36  # UUID4

    def test_row_exists_after_persist(self, db_session):
        section = _make_minimal_section()
        key = persist_section(section, report_id="rpt-001", db=db_session)
        row: SectionRow = db_session.get(SectionRow, key)
        assert row is not None

    def test_row_report_id_matches(self, db_session):
        key = persist_section(_make_minimal_section(), report_id="rpt-abc", db=db_session)
        row = db_session.get(SectionRow, key)
        assert row.report_id == "rpt-abc"

    def test_row_canonical_name_matches(self, db_session):
        key = persist_section(_make_minimal_section(canonical="Balance Sheet"), report_id="rpt-001", db=db_session)
        row = db_session.get(SectionRow, key)
        assert row.section_name_canonical == "Balance Sheet"

    def test_section_data_roundtrip(self, db_session):
        section = _make_minimal_section()
        key = persist_section(section, report_id="rpt-001", db=db_session)
        row = db_session.get(SectionRow, key)
        recovered = Section.model_validate(row.section_data)
        assert recovered.section_name_canonical == section.section_name_canonical
        assert recovered.content_markdown == section.content_markdown

    def test_rich_section_roundtrip_preserves_nested_elements(self, db_session):
        section = _make_rich_section()
        key = persist_section(section, report_id="rpt-002", db=db_session)
        row = db_session.get(SectionRow, key)
        recovered = Section.model_validate(row.section_data)
        assert len(recovered.tables) == 1
        assert len(recovered.charts) == 1
        assert len(recovered.footnotes) == 1
        assert recovered.tables[0].table_id == "tbl-rt-001"
        assert recovered.charts[0].interpretation == "Revenue shows consistent upward trend."
        assert recovered.footnotes[0].text == "FY2024 restated for discontinued operations."

    def test_multiple_sections_same_report_allowed(self, db_session):
        # (report_id, canonical) is NOT unique — multiple rows allowed
        s1 = _make_minimal_section(canonical="OTHER", raw="Notes Section 1")
        s2 = _make_minimal_section(canonical="OTHER", raw="Notes Section 2")
        k1 = persist_section(s1, report_id="rpt-multi", db=db_session)
        k2 = persist_section(s2, report_id="rpt-multi", db=db_session)
        assert k1 != k2  # distinct keys

    def test_two_different_reports_get_different_keys(self, db_session):
        section = _make_minimal_section()
        k1 = persist_section(section, report_id="rpt-X", db=db_session)
        k2 = persist_section(section, report_id="rpt-Y", db=db_session)
        assert k1 != k2


# ---------------------------------------------------------------------------
# get_section tests
# ---------------------------------------------------------------------------

class TestGetSection:
    def test_fetch_by_key_returns_section(self, db_session):
        section = _make_minimal_section()
        key = persist_section(section, report_id="rpt-001", db=db_session)
        recovered = get_section(key, db=db_session)
        assert isinstance(recovered, Section)

    def test_fetched_section_fields_match_original(self, db_session):
        section = _make_minimal_section(canonical="Balance Sheet")
        key = persist_section(section, report_id="rpt-001", db=db_session)
        recovered = get_section(key, db=db_session)
        assert recovered.section_name_canonical == "Balance Sheet"
        assert recovered.page_range == (5, 8)

    def test_missing_key_raises_key_error(self, db_session):
        with pytest.raises(KeyError):
            get_section("00000000-0000-0000-0000-000000000000", db=db_session)

    def test_rich_section_nested_elements_survive_roundtrip(self, db_session):
        section = _make_rich_section()
        key = persist_section(section, report_id="rpt-003", db=db_session)
        recovered = get_section(key, db=db_session)
        assert recovered.tables[0].rows[0].cells[0].value == 1000
        assert recovered.footnotes[0].marker == "1"


# ---------------------------------------------------------------------------
# get_sections_by_report tests
# ---------------------------------------------------------------------------

class TestGetSectionsByReport:
    def test_returns_all_sections_for_report(self, db_session):
        for i in range(3):
            persist_section(
                _make_minimal_section(raw=f"Section {i}"),
                report_id="rpt-batch",
                db=db_session,
            )
        db_session.flush()
        sections = get_sections_by_report("rpt-batch", db=db_session)
        assert len(sections) == 3

    def test_returns_empty_list_for_unknown_report(self, db_session):
        sections = get_sections_by_report("rpt-does-not-exist", db=db_session)
        assert sections == []

    def test_only_returns_sections_for_requested_report(self, db_session):
        persist_section(_make_minimal_section(), report_id="rpt-A", db=db_session)
        persist_section(_make_minimal_section(), report_id="rpt-B", db=db_session)
        db_session.flush()
        sections = get_sections_by_report("rpt-A", db=db_session)
        assert len(sections) == 1

    def test_returns_list_of_section_instances(self, db_session):
        persist_section(_make_minimal_section(), report_id="rpt-type-check", db=db_session)
        db_session.flush()
        sections = get_sections_by_report("rpt-type-check", db=db_session)
        assert all(isinstance(s, Section) for s in sections)
