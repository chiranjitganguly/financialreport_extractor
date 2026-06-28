# Agent 3: Persistence & Indexing — Implementation Design

**Status**: Ready for code generation. This is the first agent that needs real running infrastructure (Postgres with the `pgvector` extension), not just Python libraries — see §0 for local dev setup.

---

## 0. Decisions locked in for this agent (flagged, not silently assumed)

| Decision | Choice | Why |
|---|---|---|
| Document DB | **Postgres, JSONB column** | Postgres is already running for the HITL queue (Agent 1) — one database instead of three |
| Vector DB | **Postgres + `pgvector` extension** | Same instance, same reasoning — avoids standing up Pinecone/Qdrant/Chroma as a separate service |
| Embedding model | **OpenAI `text-embedding-3-small`**, via `langchain-openai` — **confirmed** | Already using OpenAI for GPT-4o; avoids a second model-serving path |
| Vector store integration | `langchain_postgres.PGVector` | Official LangChain Postgres vector store package, handles the embedding + metadata-filtered similarity search interface Agent 5 will need later |
| Migrations | **Alembic** | The schema will keep growing across agents (sections table now, an extraction ledger table later) — establish migrations now rather than hand-editing tables as you go |

### Local dev setup

```yaml
# docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16   # official pgvector-enabled Postgres image
    environment:
      POSTGRES_DB: kpi_extraction
      POSTGRES_USER: dev
      POSTGRES_PASSWORD: dev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:
```
Run `CREATE EXTENSION IF NOT EXISTS vector;` once against the database (Alembic's first migration should do this, not a manual step).

---

## 1. What Agent 3 actually does

Two jobs: persist each `Section` (with its tables/charts/footnotes nested inside) to the Document DB for later whole-section retrieval, and chunk + embed everything into the Vector DB for later semantic search.

---

## 2. Shared DB setup — `common/db.py`

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

def get_engine():
    """Reads DATABASE_URL from settings, returns a SQLAlchemy engine. Shared
    by every agent that touches Postgres — don't create separate engines
    per agent."""

SessionLocal = sessionmaker(bind=get_engine())
```

All agents that read or write Postgres (Agent 1's HITL queue, Agent 3 here, and later Agents 4/5/6 reading these tables) should import `get_engine()`/`SessionLocal` from here rather than each opening their own connection.

---

## 3. Document DB — `documents.py`

### 3.1 Table

```python
# common/storage_models.py  (SQLAlchemy models — distinct from the Pydantic
#                             contracts in common/schemas.py, since these
#                             describe storage, not the wire format)

class SectionRow(Base):
    __tablename__ = "sections"
    id: Mapped[str] = mapped_column(primary_key=True)   # UUID
    report_id: Mapped[str] = mapped_column(index=True)
    section_name_canonical: Mapped[str] = mapped_column(index=True)
    section_data: Mapped[dict] = mapped_column(JSONB)    # the full Section
                                                           # object, serialized
                                                           # exactly per
                                                           # common/schemas.py
    created_at: Mapped[datetime]
```

**Design note**: `(report_id, section_name_canonical)` is indexed for lookup but **not** a uniqueness constraint — more than one raw section can legitimately align to the same canonical name (e.g. two distinct `"OTHER"` sections), or in rarer cases to the same real canonical name if a report repeats a statement. Don't add a unique constraint that would reject that.

The whole `Section` (including its nested `tables`/`charts`/`footnotes` lists) is stored as one JSONB blob per row, not normalized into child tables — Agent 4 and Agent 6 consume this as a Python object via Pydantic, not via SQL joins over individual table rows, so normalizing it would add complexity with no real benefit here.

### 3.2 Functions

```python
def persist_section(section: Section, report_id: str) -> str:
    """
    Args:
        section: a fully-assembled Section from Agent 2's output
        report_id: the report this belongs to

    Returns:
        the generated row id (UUID string) — this is the "document key"
        the main spec's Agent 3 output description refers to

    Implementation: section.model_dump_json() into the JSONB column via the
    SectionRow model above. One INSERT per section — no need to batch for
    a single report's section count (typically well under 100).
    """

def get_section(document_key: str) -> Section:
    """
    Args:
        document_key: id returned by persist_section()

    Returns:
        Section, reconstructed via Section.model_validate(row.section_data)

    Used by Agent 4 (Tier 1) and Agent 6 (Tier 3) to fetch full section
    content during extraction.
    """

def get_sections_by_report(report_id: str) -> list[Section]:
    """
    Bulk fetch — used when an agent needs every section for a report
    (e.g. Agent 4 iterating across all of a KPI's canonical_sections).
    """
```

---

## 4. Chunking — `chunking.py`

Four chunk types, one function per type, all returning the same `TextChunk` shape so the embedding step downstream doesn't need to branch on type.

```python
class TextChunk(BaseModel):
    chunk_text: str
    source_element_type: Literal["text", "table_row", "chart_interpretation", "footnote"]
    section_name_canonical: str
    page: Optional[int]            # specific page for table_row/chart/footnote chunks
    page_range: Optional[tuple[int, int]]  # for narrative text chunks, which span a range
    element_ref: Optional[str]      # table_id / chart_id / footnote_id, when applicable —
                                     # lets Tier 2 trace a retrieved chunk back to its source
                                     # element for source_element_type tagging on the
                                     # eventual ExtractionRecord

def chunk_narrative_text(section: Section) -> list[TextChunk]:
    """
    Splits section.content_markdown using LangChain's
    RecursiveCharacterTextSplitter (chunk_size and chunk_overlap from
    config — see §6). page_range on each chunk is the section's own
    page_range, since narrative text isn't chunked with per-chunk page
    tracking at this granularity (Docling's page provenance is at the
    section/element level, not mid-paragraph) — accept this as an
    acceptable precision loss for narrative chunks specifically; tables/
    charts/footnotes all carry an exact page already.
    """

def chunk_table(table: TableElement) -> list[TextChunk]:
    """
    One chunk per row by default — most KPI lookups target a specific
    line item, so row-level granularity matches how Tier 2 will query.
    Serialize each row as: "{row_label} — {column_label}: {value}; ..."
    across all columns in that row, e.g.
    "Revenue — FY2025: 1,234; FY2024: 1,100"
    (matches the exact example already given in the main spec's Agent 3
    description — keep this serialization format consistent with that).
    element_ref = table.table_id, page = table.page for every row chunk
    (Docling table extraction is page-level, not per-row).
    """

def chunk_chart(chart: ChartElement) -> list[TextChunk]:
    """
    One chunk: chart.interpretation text (the chart's image isn't
    embeddable as text — only its generated description is).
    element_ref = chart.chart_id, page = chart.page.
    """

def chunk_footnote(footnote: FootnoteElement) -> list[TextChunk]:
    """
    One chunk: footnote.text. element_ref = footnote.footnote_id,
    page = footnote.page.
    """

def chunk_section(section: Section) -> list[TextChunk]:
    """
    Convenience wrapper: calls all four functions above for one section
    and returns the combined list. This is what pipeline.py actually
    calls per section, not the four functions individually.
    """
```

---

## 5. Embedding + Vector DB — `embeddings.py`

```python
def embed_and_store_chunks(chunks: list[TextChunk], report_id: str) -> int:
    """
    Args:
        chunks: output of chunk_section(), across however many sections
                are being processed in this call
        report_id: tagged into every chunk's metadata for later filtering

    Logic: build a langchain_postgres.PGVector instance (connection from
    common/db.py, embedding model from config — see §6), and add each
    chunk's chunk_text with metadata = {report_id, section_name_canonical,
    source_element_type, page, page_range, element_ref}. Use add_texts()
    in one batched call across all chunks passed in, not one call per
    chunk — reduces API round-trips to the embedding model.

    Returns:
        number of chunks successfully embedded and stored (for logging/
        verification — the main spec's "vector collection/namespace id"
        output maps to report_id here, since pgvector via one shared
        table doesn't need separate named collections; report_id in the
        metadata IS the namespace, filtered on at query time).
    """
```

---

## 6. Configuration additions — `config.py`

```python
class Settings(BaseSettings):
    # ... existing settings from Agents 1 and 2 ...
    DATABASE_URL: str
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    CHUNK_SIZE: int = 500          # characters, for narrative text splitting
    CHUNK_OVERLAP: int = 50
```

---

## 7. Top-level orchestration — `pipeline.py`

```python
class Agent3Output(BaseModel):
    document_keys: list[str]     # one per persisted section
    chunks_embedded: int

def run_agent3(sections: list[Section], report_id: str) -> Agent3Output:
    """
    Steps:
        1. For each section: document_keys.append(persist_section(section, report_id))
        2. all_chunks = [chunk for section in sections for chunk in chunk_section(section)]
        3. chunks_embedded = embed_and_store_chunks(all_chunks, report_id)

    Returns:
        Agent3Output

    Note: this agent has no LLM calls and no confidence/HITL routing of its
    own — it's pure persistence. Keep it that way; don't add classification
    or validation logic here that belongs in a later agent.
    """
```

---

## 8. Module structure

```
common/
  db.py                  # §2 — shared engine/session
  storage_models.py       # §3.1 — SQLAlchemy models (SectionRow, and later
                          #   tables as Agents 4+ need persistence too)
  schemas.py               # (existing, from Agent 2's refactor)
  taxonomy_map.py            # (existing)

agent3_persistence/
  __init__.py
  config.py                 # §6 additions
  documents.py                # §3.2
  chunking.py                  # §4
  embeddings.py                  # §5
  pipeline.py                     # §7

alembic/
  versions/
    0001_enable_pgvector_and_sections_table.py   # CREATE EXTENSION vector;
                                                   # + sections table

tests/
  fixtures/
    sample_sections.json     # a few synthetic Section objects (reuse Agent 2's
                              # synthetic fixtures, or build new ones with
                              # populated tables/charts/footnotes specifically
                              # to exercise all four chunking functions)
  test_documents.py            # persist/retrieve round-trip
  test_chunking.py               # one test per chunk type, checking serialization
  test_embeddings.py               # mock the embedding call, verify metadata is correct
  test_pipeline.py
```

---

## 9. Open items

- ~~**Embedding model decision**~~ — confirmed: OpenAI `text-embedding-3-small`.
- ~~**Document DB normalization (whole-object JSONB vs. normalized tables)**~~ — confirmed: whole `Section` objects as JSONB, not normalized.
- **Narrative chunk page precision**: accepted as a known limitation in §4 (page_range, not exact page, for text chunks) — flag if this turns out to matter for citation accuracy once Tier 3 starts using retrieved chunks for page references.
- **Chunk size/overlap tuning**: 500/50 are reasonable starting defaults, not validated against your actual report documents yet — expect to tune once real Vector DB queries are running in Agent 5.
- **Alembic migration ownership**: since Agent 1's HITL queue tables and Agent 3's sections/embeddings tables are both in the same Postgres instance, make sure whichever agent's migration runs first doesn't assume it owns the whole schema — keep migrations additive and agent-attributable (e.g. filename prefixes indicating which agent introduced each table).
