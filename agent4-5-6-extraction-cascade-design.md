# Agents 4, 5, 6 + Step 6a: The Extraction Cascade — Implementation Design

**Status**: Ready for code generation. These three agents are designed together because the spec requires it — Tier 2 explicitly reuses Tier 1's matching logic, and the discrepancy-resolution step (6a) is callable from any of the three. Building them in isolation would mean either duplicating logic or discovering the coupling halfway through.

---

## 0. Shared infrastructure to introduce/retrofit now

This is the largest shared-infra round yet — three separate things need a common home before any tier-specific code is written.

### 0.1 `common/schemas.py` — add `ExtractionRecord` and `ExtractionLedger`

These exist as JSON contracts in the main spec but have never been implemented as Pydantic models — Agent 4 is the first real consumer.

```python
class ConflictingValue(BaseModel):
    section: str
    value: str | float
    method: Literal["deterministic", "semantic", "llm"]
    source_element_type: Literal["text", "table_cell", "chart"]

class AttemptRecord(BaseModel):
    tier: Literal["deterministic", "semantic", "llm"]
    value: str | float | None
    confidence: float
    outcome: Literal["found", "not_found", "flagged"]
    note: str = ""

class ExtractionRecord(BaseModel):
    kpi_id: str
    value: str | float | None = None
    fiscal_year: str
    section: Optional[str] = None
    page: Optional[int] = None
    method: Optional[Literal["deterministic", "semantic", "llm"]] = None
    source_element_type: Optional[Literal["text", "table_cell", "chart"]] = None
    footnotes: list[str] = []
    confidence: float = 0.0
    status: Literal["found", "not_found", "flagged", "needs_human_review"] = "not_found"
    review_reason: Optional[Literal[
        "low_confidence", "validation_failed", "not_found_after_retries",
        "section_discrepancy", "footnoted_caveat"
    ]] = None
    conflicting_values: list[ConflictingValue] = []
    attempts: list[AttemptRecord] = []

class ExtractionLedger(BaseModel):
    records: dict[str, ExtractionRecord]   # keyed by kpi_id
```

### 0.2 `common/taxonomy_map.py` — add the applicability filter and ledger initializer

```python
def filter_applicable_taxonomy(
    taxonomy: list[TaxonomyEntry],
    report_metadata: ReportMetadata,
) -> list[TaxonomyEntry]:
    """
    Returns only the TaxonomyEntry rows applicable to this report, per the
    main spec's rule: an empty/missing applicable_industries /
    applicable_report_types / applicable_accounting_standards means the
    KPI applies to ALL values for that dimension.

    Args:
        taxonomy: full load_taxonomy_map() output
        report_metadata: this report's industry / report_type / accounting_standard

    Returns:
        filtered list[TaxonomyEntry] — every entry where each populated
        applicability field either is empty OR contains the report's value
        for that dimension.
    """

def initialize_extraction_ledger(filtered_taxonomy: list[TaxonomyEntry], fiscal_year: str) -> ExtractionLedger:
    """
    Args:
        filtered_taxonomy: output of filter_applicable_taxonomy()
        fiscal_year: report_metadata.fiscal_year, stamped onto every record

    Returns:
        ExtractionLedger with one ExtractionRecord per taxonomy entry,
        every one status="not_found", confidence=0.0 — the starting state
        the whole cascade operates on.
    """
```

### 0.3 `common/llm_client.py` — promote Agent 1's local client to shared

Agent 1, Agent 2 (section-alignment LLM fallback), and now Agent 4/5/6 all need a `ChatOpenAI` client. Stop re-instantiating it per agent:

```python
def get_llm_client(temperature: float = 0.0) -> ChatOpenAI:
    """
    Shared client factory — .with_retry() applied here once, so every
    caller gets retry behavior automatically rather than re-wrapping it.
    If Agent 1's llm_client.py still exists as an agent-local module,
    move its contents here and update Agent 1's (and Agent 2's) imports —
    do this now, not after a fourth copy gets written.
    """
```

### 0.4 `common/deterministic_matching.py` — shared by Tier 1 AND Tier 2

The main spec is explicit that Tier 2 uses "the SAME deterministic/pattern logic as Tier 1" — this is an instruction not to duplicate, not a coincidence of similar design.

```python
class CandidateValue(BaseModel):
    value: str | float
    section_name_canonical: str
    page: Optional[int]
    source_element_type: Literal["text", "table_cell"]
    footnotes: list[str] = []
    confidence: float

def compute_match_confidence(num_candidates_considered: int, top_score: float, runner_up_score: Optional[float]) -> float:
    """
    Shared confidence formula, per the main spec's resolved policy:
    1.0 for an unambiguous single match; 0.5–0.7 when a heuristic had to
    tie-break multiple candidates.

    Args:
        num_candidates_considered: how many rows/text-spans matched the
                                    KPI name/aliases at all, before picking one
        top_score: the winning candidate's match score (rapidfuzz 0-100, or
                    1.0 for an exact regex hit — normalize to 0-1 before
                    calling this function)
        runner_up_score: the second-best candidate's score, if any

    Returns:
        1.0 if num_candidates_considered == 1.
        Otherwise, a value in 0.5-0.7, scaled by how close the runner-up
        was (closer runner-up = lower confidence within that range).
    """

def match_table_row(
    table: TableElement,
    kpi_name: str,
    aliases: list[str],
    fiscal_year: str,
    fuzzy_cutoff: float,
) -> Optional[CandidateValue]:
    """
    Fuzzy-match every row's row_label against kpi_name + aliases (rapidfuzz
    WRatio — same library, same scorer choice as Agent 1's company lookup
    and Agent 2's section alignment; this codebase has one fuzzy-matching
    convention, not three).

    On a match clearing fuzzy_cutoff: read the cell whose column_label
    matches fiscal_year (not an adjacent comparative-period column).

    Returns:
        CandidateValue(source_element_type="table_cell", page=table.page,
        footnotes=matched_cell.footnote_refs, confidence=
        compute_match_confidence(...)) or None if nothing clears the cutoff.
    """

def match_narrative_text(
    content_markdown: str,
    kpi_name: str,
    aliases: list[str],
    section_page_range: tuple[int, int],
) -> Optional[CandidateValue]:
    """
    Regex search for a KPI name/alias followed by a number within close
    proximity (e.g. same line or sentence). Build the pattern from
    kpi_name + each alias, case-insensitive.

    Known limitation: content_markdown carries no per-line page numbers
    (only the section's overall page_range is known) — set page =
    section_page_range[0] as an approximation. This mirrors the same
    precision loss already accepted for narrative chunks in Agent 3.

    Returns:
        CandidateValue(source_element_type="text", confidence=
        compute_match_confidence(...) based on how many distinct
        line/sentence matches were found) or None.
    """
```

### 0.5 `common/discrepancy_resolution.py` — Step 6a, callable from any tier

```python
def resolve_cross_section_discrepancy(
    taxonomy_entry: TaxonomyEntry,
    candidates: list[CandidateValue],
    sections_involved: list[Section],
) -> ExtractionRecord:
    """
    Args:
        taxonomy_entry: the KPI in question
        candidates: 2+ CandidateValues with disagreeing values, gathered by
                    whichever tier detected them (Tier 1, 2, or 3)
        sections_involved: the full Section objects each candidate came
                            from, so the LLM gets real context, not just
                            the bare conflicting numbers

    Logic: single (non-batched) GPT-4o structured-output call — present
    every candidate (value, section, source_element_type) plus each
    section's relevant content, and ask the model to determine the
    authoritative value.

    Returns:
        ExtractionRecord with:
        - value = the LLM's chosen value
        - method = "llm", source_element_type = whatever the LLM indicates
          as the winning source
        - status = "needs_human_review", review_reason = "section_discrepancy"
        - conflicting_values = every candidate NOT chosen, converted to
          ConflictingValue entries

    This call does NOT consume the shared 2-turn retry budget (per the
    main spec) — there's no budget parameter here at all, unlike Agent 2's
    retry functions. It's a one-shot, inline escalation, called directly
    by whichever tier found the disagreement.
    """
```

---

## 1. Agent 4 — Tier 1: Deterministic Extraction

```python
# agent4_tier1/extraction.py

def extract_tier1_candidates_for_kpi(
    taxonomy_entry: TaxonomyEntry,
    sections_by_canonical_name: dict[str, list[Section]],
    fiscal_year: str,
    fuzzy_cutoff: float,
) -> list[CandidateValue]:
    """
    Args:
        taxonomy_entry: the KPI being extracted
        sections_by_canonical_name: all of this report's sections, grouped
                                     by section_name_canonical (more than
                                     one Section can share a canonical name —
                                     see Agent 2's design note — check all of them)
        fiscal_year: report_metadata.fiscal_year
        fuzzy_cutoff: config.TABLE_ROW_FUZZY_CUTOFF

    Logic: for EVERY canonical section name in taxonomy_entry.canonical_sections
    (not just the first match — fetch all, per the main spec), and for every
    Section object under that name:
        1. Try match_table_row() on each of the section's tables
        2. If no table match, try match_narrative_text() on its content_markdown
    Collect every CandidateValue found — do not stop early. This exhaustive
    check across all candidate sections is specifically what makes
    cross-section discrepancy detection possible within Tier 1 itself.

    Returns:
        list[CandidateValue], possibly empty, possibly containing
        disagreeing values from different sections.
    """

def run_tier1(
    ledger: ExtractionLedger,
    filtered_taxonomy: list[TaxonomyEntry],
    sections: list[Section],
    fiscal_year: str,
) -> ExtractionLedger:
    """
    For every ledger entry with status == "not_found":
        candidates = extract_tier1_candidates_for_kpi(...)
        distinct_values = set of unique `value` across candidates

        - 0 candidates -> leave as not_found (flows to Tier 2)
        - 1 distinct value (even if found in >1 section, all agreeing) ->
          mark status="found", method="deterministic", populate from the
          (first) candidate
        - >1 distinct value -> call resolve_cross_section_discrepancy(),
          use its returned ExtractionRecord directly (status already set
          to needs_human_review by that function)

    Returns:
        updated ExtractionLedger
    """
```

---

## 2. Agent 5 — Tier 2: Semantic Retrieval

```python
# agent5_tier2/retrieval.py

def search_vector_chunks(
    kpi_name: str,
    aliases: list[str],
    canonical_sections: list[str],
    report_id: str,
    top_k: int,
) -> list[TextChunk]:
    """
    Query langchain_postgres.PGVector.similarity_search() with:
        query = kpi_name + " " + " ".join(aliases)
        filter = {"report_id": report_id, "section_name_canonical": {"$in": canonical_sections}}
        k = top_k

    Returns:
        list[TextChunk] (reconstructed from the vector store's returned
        documents + metadata, matching common/schemas.py's TextChunk —
        including structured_data for table_row chunks, per Agent 3's
        updated schema)
    """

def extract_from_chunk(
    chunk: TextChunk,
    kpi_name: str,
    aliases: list[str],
    fiscal_year: str,
) -> Optional[CandidateValue]:
    """
    Dispatches based on chunk.source_element_type, reusing
    common/deterministic_matching.py — this function does NOT contain its
    own matching logic, it routes to the shared functions:

    - "table_row": read chunk.structured_data directly (row_label, cells
      dict) — match row_label against kpi_name/aliases, pick the
      fiscal_year cell. (Conceptually the same as match_table_row(), but
      operating on one already-retrieved row rather than scanning a whole
      TableElement — implement as a thin variant reusing
      compute_match_confidence(), don't duplicate the matching logic itself.)
    - "text", "chart_interpretation", "footnote": call
      match_narrative_text(chunk.chunk_text, kpi_name, aliases, ...) directly
      — chart interpretations and footnote text are just narrative text as
      far as pattern matching is concerned.

    Returns:
        CandidateValue or None
    """

def extract_tier2_candidates_for_kpi(
    taxonomy_entry: TaxonomyEntry,
    report_id: str,
    fiscal_year: str,
    top_k: int,
) -> list[CandidateValue]:
    """
    chunks = search_vector_chunks(taxonomy_entry.kpi_name, taxonomy_entry.aliases,
                                    taxonomy_entry.canonical_sections, report_id, top_k)
    candidates = [extract_from_chunk(c, ...) for c in chunks], filtering Nones,
    then deduplicated by (section_name_canonical, value) — multiple chunks
    from the same section agreeing on the same value isn't a new candidate,
    it's corroboration.

    Returns:
        list[CandidateValue]
    """

def run_tier2(
    ledger: ExtractionLedger,
    filtered_taxonomy: list[TaxonomyEntry],
    report_id: str,
    fiscal_year: str,
    top_k: int,
) -> ExtractionLedger:
    """
    Same structure as run_tier1() — only processes status == "not_found"
    entries, same 0/1/>1-distinct-values branching, same call into
    resolve_cross_section_discrepancy() when candidates disagree.
    method="semantic" on resolved entries (not "deterministic" — even
    though the underlying matching function is shared, the field records
    which TIER ran it, per the main spec's ExtractionRecord.method values).
    """
```

---

## 3. Agent 6 — Tier 3: LLM Extraction

```python
# agent6_tier3/extraction.py

class Tier3KPIRequest(BaseModel):
    kpi_id: str
    kpi_name: str
    aliases: list[str]
    definition: str

class Tier3ExtractionResult(BaseModel):
    kpi_id: str
    found: bool
    value: Optional[str | float] = None
    page: Optional[int] = None
    source_element_type: Optional[Literal["text", "table_cell", "chart"]] = None
    footnote_ids: list[str] = []
    confidence: float = 0.0

def build_section_context_for_prompt(section: Section) -> str:
    """
    Assembles section.content_markdown + serialized tables (markdown table
    format, not the JSON structure — the LLM reads markdown tables fine and
    it's far more token-efficient than raw JSON) + footnote text + chart
    content.

    Chart handling: if section.charts is non-empty, include each chart's
    interpretation text in the prompt context as a labeled block ("Chart:
    {caption} — {interpretation}"). NOTE: Agent 1b doesn't exist yet, so
    section.charts will be an empty list in practice until it's built —
    this function should handle that gracefully (it already does, by
    construction — just don't write a vision-input code path now, since
    there's nothing to feed it. See Open Items on deferring that decision.)

    Returns:
        str, the assembled context block for one section
    """

def run_tier3_for_section(
    section: Section,
    kpi_requests: list[Tier3KPIRequest],
) -> list[Tier3ExtractionResult]:
    """
    ONE batched GPT-4o structured-output call for this section, listing
    every kpi_request together (per the main spec's batching decision).
    Build the output schema as a list of Tier3ExtractionResult — one
    per requested KPI, explicit per-KPI instructions in the prompt (not
    one generic instruction) so accuracy doesn't degrade as the batch
    grows.

    Args:
        section: full Section, output of build_section_context_for_prompt()
                 forms the prompt's context block
        kpi_requests: every still-unresolved KPI whose canonical_sections
                      includes this section

    Returns:
        list[Tier3ExtractionResult], same length/order as kpi_requests
    """

def run_tier3(
    ledger: ExtractionLedger,
    filtered_taxonomy: list[TaxonomyEntry],
    sections: list[Section],
    fiscal_year: str,
) -> ExtractionLedger:
    """
    Unlike Tier 1/2, this tier batches by SECTION, not by KPI — so the
    collection logic is inverted:

    1. Build section -> list[Tier3KPIRequest] groups: for each not_found
       KPI, for each of its canonical_sections that has a matching Section
       object, add a Tier3KPIRequest to that section's group.
    2. For each section group: results = run_tier3_for_section(section, requests)
    3. Collect results PER KPI across however many section-groups it
       appeared in (a KPI with 2 canonical_sections gets up to 2 results,
       one per section, which might disagree).
    4. Per KPI: candidates = [CandidateValue from each found
       Tier3ExtractionResult]; same 0/1/>1-distinct-values branching as
       Tier 1/2 — >1 distinct triggers resolve_cross_section_discrepancy().
    5. KPIs with zero found results across every section group ->
       status="not_found" (terminal — no further tiers).

    Returns:
        updated ExtractionLedger
    """
```

---

## 4. The cascade — top-level orchestration

This spans all three agents and doesn't belong inside any single one of them.

```python
# extraction_cascade.py  (repo root, or a small extraction_orchestration/ package)

def run_extraction_cascade(
    filtered_taxonomy: list[TaxonomyEntry],
    sections: list[Section],
    report_metadata: ReportMetadata,
    report_id: str,
) -> ExtractionLedger:
    """
    Args:
        filtered_taxonomy: filter_applicable_taxonomy() output
        sections: Agent 2's output for this report
        report_metadata, report_id: this report's identifiers

    Steps:
        1. ledger = initialize_extraction_ledger(filtered_taxonomy, report_metadata.fiscal_year)
        2. sections_by_canonical_name = group sections by section_name_canonical
        3. ledger = run_tier1(ledger, filtered_taxonomy, sections, report_metadata.fiscal_year)
        4. ledger = run_tier2(ledger, filtered_taxonomy, report_id, report_metadata.fiscal_year, top_k)
        5. ledger = run_tier3(ledger, filtered_taxonomy, sections, report_metadata.fiscal_year)

    Returns:
        ExtractionLedger — passed to Agent 7 (Validator) next, which isn't
        built yet. This function is the cascade's entry point; it does NOT
        run the Validator/Retry Controller loop — that's the next agent
        you'll build, not this one.
    """
```

Note this function takes the cascade exactly once through Tiers 1→2→3 — it has no retry logic of its own. The shared 2-turn retry budget and the Validator/Retry Controller loop (Agents 7/8) operate on top of this cascade's output, re-invoking individual tiers for specific flagged KPIs later. Don't build that logic here; it belongs to Agent 7/8 when you get there.

---

## 5. Configuration additions

```python
class Settings(BaseSettings):
    # ... existing settings ...
    TABLE_ROW_FUZZY_CUTOFF: float = 85.0       # rapidfuzz 0-100, Tier 1 table row matching
    SEMANTIC_TOP_K: int = 5                     # Tier 2 vector search result count
```

---

## 6. Module structure

```
common/
  schemas.py                  # + ExtractionRecord, ExtractionLedger, ConflictingValue, AttemptRecord
  taxonomy_map.py               # + filter_applicable_taxonomy(), initialize_extraction_ledger()
  llm_client.py                  # promoted from Agent 1 (§0.3)
  deterministic_matching.py        # §0.4 — shared by Tier 1 and Tier 2
  discrepancy_resolution.py          # §0.5 — Step 6a

agent4_tier1/
  __init__.py
  extraction.py                # §1

agent5_tier2/
  __init__.py
  retrieval.py                  # §2

agent6_tier3/
  __init__.py
  prompts.py                     # Tier 3's batched-per-section prompt templates
  extraction.py                    # §3

extraction_cascade.py            # §4 — top-level entry point spanning all three

tests/
  fixtures/
    sample_taxonomy_map.json       # synthetic — KPIs with single AND multiple canonical_sections,
                                    #   to exercise discrepancy detection deliberately
  test_deterministic_matching.py     # match_table_row / match_narrative_text in isolation
  test_discrepancy_resolution.py       # mocked LLM call, verify conflicting_values populated correctly
  test_tier1.py
  test_tier2.py                          # mock the vector store, not a real pgvector query
  test_tier3.py                            # mocked LLM, verify per-section batching groups KPIs correctly
  test_extraction_cascade.py                 # end-to-end with all three tiers, mocked LLM/vector calls
```

---

## 7. Open items

- **Chart vision-input decision deferred, not resolved**: since Agent 1b doesn't exist, `section.charts` is always empty in current practice, so Tier 3's chart-handling code path is untestable for real until then. `build_section_context_for_prompt()` is written to degrade gracefully (empty charts list = no chart block in the prompt), but the actual vision-vs-interpretation-text decision flagged back in the Agent 1b design discussion is still genuinely open — revisit it when Agent 1b is actually built, not now.
- **Tier 1/Tier 2 confidence formula (`compute_match_confidence`)**: the 0.5–0.7 range for tie-broken matches is a starting heuristic, not empirically validated — expect to tune once real extraction results are reviewed against real documents.
- **Tier 3 batching limits**: no cap is specified on how many KPIs can go into one section's batched prompt — if a section is relevant to a large number of unresolved KPIs, one call might get unwieldy. Worth a sanity check against real taxonomy size; add a batch-size cap with multiple calls per section if needed, but don't build that complexity preemptively without evidence it's needed.
- **Tier 2 chunk deduplication**: "multiple chunks from the same section agreeing on the same value isn't a new candidate" — confirm this dedup-by-(section, value) approach doesn't lose useful corroboration signal you might want to factor into confidence scoring later (currently it doesn't feed into `compute_match_confidence` at all).
