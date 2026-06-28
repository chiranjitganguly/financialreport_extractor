import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from report_ingestion import hitl_queue
from report_ingestion.persistence import load_agent_run
from report_ingestion.pipeline import ingest_input_dir, run_report_ingestion
from report_ingestion.schemas import ReportIngestionOutput


@asynccontextmanager
async def lifespan(app: FastAPI):
    await hitl_queue.init_db()
    yield


app = FastAPI(title="KPI Extractor", lifespan=lifespan)


@app.post("/ingest", response_model=dict[str, ReportIngestionOutput])
async def ingest():
    """Scan the configured input directory and process every report found."""
    try:
        return await ingest_input_dir()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/reports/{report_id}")
async def get_report(report_id: str):
    """Return the most recent persisted Agent 1 run for a report."""
    run = await load_agent_run(report_id, "agent1")
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run found for report_id '{report_id}'")
    return run


@app.get("/reports/{report_id}/sections")
async def get_report_sections(report_id: str):
    """Return the most recent persisted Agent 2 run (section list) for a report.

    The ``metadata`` field contains summary stats (section_count,
    has_unresolved_sections, retry_turns_used, sections_path).  The full
    sections JSON is stored on disk at ``sections_path`` and can be read
    directly if needed.
    """
    run = await load_agent_run(report_id, "agent2")
    if run is None:
        raise HTTPException(status_code=404, detail=f"No Agent 2 run found for report_id '{report_id}'")
    return run


class ResolveRequest(BaseModel):
    corrections: dict[str, str]


@app.post("/reports/{report_id}/resolve")
async def resolve(report_id: str, body: ResolveRequest):
    """Apply human corrections to a report awaiting review and resume it."""
    try:
        metadata = await hitl_queue.resolve_review(report_id, body.corrections)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"report_id": report_id, "report_metadata": metadata.model_dump()}
