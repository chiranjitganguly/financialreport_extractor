import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from report_ingestion.config import settings
from report_ingestion.hitl_queue import get_pool

log = logging.getLogger(__name__)


async def save_agent_run(report_id: str, agent_name: str, output: BaseModel) -> None:
    """Persist any agent's output for audit and pipeline continuity.

    Large payload fields are written to flat files under OUTPUT_DIR to avoid
    multi-MB JSONB blobs.  The ``narrative_path`` column in ``agent_runs``
    holds the path to whichever output file was written (one per agent run):

    * Agent 1 — ``narrative_markdown`` → ``agent1_narrative.md``
    * Agent 2 — ``sections`` list      → ``agent2_sections.json``

    The DB ``metadata`` column stores only lightweight summary fields plus the
    path to the output file, so the full output can be reconstructed by reading
    that file.

    Args:
        report_id:  Unique identifier for the report.
        agent_name: Canonical agent identifier, e.g. ``"agent1"`` / ``"agent2"``.
        output:     Any Pydantic model returned by an agent's run function.
    """
    out_dir = Path(settings.OUTPUT_DIR) / report_id
    out_dir.mkdir(parents=True, exist_ok=True)

    output_dict: dict[str, Any] = output.model_dump()
    output_file_path: str | None = None

    # --- Agent 1: narrative markdown → flat text file ---
    narrative_markdown: str = output_dict.pop("narrative_markdown", None) or ""
    if narrative_markdown:
        output_file_path = str(out_dir / f"{agent_name}_narrative.md")
        Path(output_file_path).write_text(narrative_markdown, encoding="utf-8")

    # --- Agent 2: sections list → JSON file ---
    sections: list | None = output_dict.pop("sections", None)
    if sections is not None:
        output_file_path = str(out_dir / f"{agent_name}_sections.json")
        Path(output_file_path).write_text(
            json.dumps(sections, indent=2, default=str), encoding="utf-8"
        )
        # Keep a lightweight summary in the DB metadata column.
        output_dict["section_count"] = len(sections)
        output_dict["sections_path"] = output_file_path

    status: str = output_dict.get("status", "completed")
    metadata_json = json.dumps(output_dict)

    # Agent 1 stored flagged_fields as a separate column for easy querying;
    # keep that for backward compat and use an empty list for other agents.
    flagged_fields = output_dict.get("flagged_fields", [])
    flagged_json = json.dumps(flagged_fields)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agent_runs
                (report_id, agent_name, status, metadata, flagged_fields, narrative_path)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            report_id,
            agent_name,
            status,
            metadata_json,
            flagged_json,
            output_file_path,
        )

    log.info(
        "Saved %s run for report_id=%s status=%s output_file=%s",
        agent_name, report_id, status, output_file_path,
    )


async def load_agent_run(report_id: str, agent_name: str) -> dict | None:
    """Return the most recent persisted run for the given report and agent.

    Args:
        report_id:  Report to look up.
        agent_name: Which agent's output to fetch.

    Returns:
        Dict with keys ``status``, ``metadata``, ``flagged_fields``,
        ``narrative_path``, ``created_at``, or ``None`` if no run exists.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status, metadata, flagged_fields, narrative_path, created_at
              FROM agent_runs
             WHERE report_id = $1 AND agent_name = $2
             ORDER BY created_at DESC
             LIMIT 1
            """,
            report_id,
            agent_name,
        )

    if row is None:
        return None

    return {
        "status": row["status"],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
        "flagged_fields": json.loads(row["flagged_fields"]),
        "narrative_path": row["narrative_path"],
        "created_at": row["created_at"].isoformat(),
    }
