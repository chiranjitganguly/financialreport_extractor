import json

import asyncpg

from report_ingestion.config import settings
from report_ingestion.schemas import FieldReview, ReportMetadata

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.DATABASE_URL)
    return _pool


async def init_db() -> None:
    """Create the HITL tables if they do not already exist.

    Call once at application startup before any pipeline work begins.
    Safe to call repeatedly — uses ``CREATE TABLE IF NOT EXISTS``.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id             BIGSERIAL    PRIMARY KEY,
                report_id      TEXT         NOT NULL,
                agent_name     TEXT         NOT NULL,
                status         TEXT         NOT NULL,
                metadata       JSONB,
                flagged_fields JSONB,
                narrative_path TEXT,
                created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_report_id ON agent_runs (report_id)"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                report_id       TEXT        PRIMARY KEY,
                status          TEXT        NOT NULL DEFAULT 'awaiting_input',
                resolved_fields JSONB,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_queue (
                id              BIGSERIAL   PRIMARY KEY,
                report_id       TEXT        NOT NULL REFERENCES reports (report_id),
                flagged_fields  JSONB       NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


async def enqueue_for_review(
    report_id: str,
    resolved_fields: dict,
    flagged_fields: list[FieldReview],
) -> None:
    """Insert a report into the HITL review queue without blocking the pipeline.

    Writes a row to the ``review_queue`` table and sets ``reports.status =
    'awaiting_input'`` for the given report. pipeline.py returns immediately
    after calling this — the pipeline does not wait for human correction
    (async queue-and-resume per the main spec).

    Args:
        report_id: Unique identifier for the report being queued.
        resolved_fields: The classification fields that DID pass confidence
            routing, provided as context for the human reviewer.
        flagged_fields: The fields that failed confidence routing, as produced
            by route_confidence().
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO reports (report_id, status, resolved_fields)
                VALUES ($1, 'awaiting_input', $2)
                ON CONFLICT (report_id) DO UPDATE
                    SET status          = 'awaiting_input',
                        resolved_fields = EXCLUDED.resolved_fields,
                        updated_at      = NOW()
                """,
                report_id,
                json.dumps(resolved_fields),
            )
            await conn.execute(
                """
                INSERT INTO review_queue (report_id, flagged_fields)
                VALUES ($1, $2)
                """,
                report_id,
                json.dumps([f.model_dump() for f in flagged_fields]),
            )


async def resolve_review(report_id: str, corrections: dict[str, str]) -> ReportMetadata:
    """Apply human corrections and mark the report ready to resume.

    Called by the FastAPI correction endpoint, not by pipeline.py directly.

    Args:
        report_id: Which report is being resumed.
        corrections: Mapping of field_name to human-corrected value, containing
            only the fields that were flagged (not the full metadata set).

    Returns:
        Completed ReportMetadata built by merging ``corrections`` with the
        fields that had already passed confidence routing. Also flips
        ``reports.status`` to ``'resumed'`` so the Orchestrator can pick
        the report back up.

    Raises:
        ValueError: If no report with ``report_id`` exists in the database.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT resolved_fields FROM reports WHERE report_id = $1",
            report_id,
        )
        if row is None:
            raise ValueError(f"No report found with report_id '{report_id}'")

        resolved: dict = json.loads(row["resolved_fields"]) if row["resolved_fields"] else {}

        # Merge: corrections override any previously-resolved value for the same field.
        merged = {**resolved, **corrections, "report_id": report_id}

        await conn.execute(
            """
            UPDATE reports
               SET status     = 'resumed',
                   updated_at = NOW()
             WHERE report_id  = $1
            """,
            report_id,
        )

    return ReportMetadata.model_validate(merged)
