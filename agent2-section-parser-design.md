# Agent 2: Section Parser & Splitter — Implementation Design

**Status**: Ready for code generation.

---

## 0. Two structural notes before building this agent

**1. Introduce a `common/` package now.** Agent 2 needs `ReportMetadata`, `TaxonomyEntry`, `TableElement`, `ChartElement`, `FootnoteElement`, and `Section` — all defined in the main spec, but currently only instantiated as local Pydantic models inside Agent 1's `schemas.py`. These are cross-agent contracts, not Agent-1-local types. Before writing Agent 2:
- Move `ReportMetadata`, `TaxonomyEntry`, `TableElement`, `ChartElement`, `FootnoteElement`, `Section` into a new `common/schemas.py`.
- Update Agent 1's imports to pull `ReportMetadata` from `common.schemas` instead of defining it locally.
- This is the right moment to do this refactor — it gets harder the more agents exist on top of the old structure.

**2. The Taxonomy Map has no loader yet — this is the first agent that genuinely needs one.** Agent 1's design referenced "the Taxonomy Map's industry vocabulary" for its LLM fallback but never specified how it's loaded. Fix that here, since Agent 2 needs the Taxonomy Map's `canonical_sections` vocabulary just as directly:

```python
# common/taxonomy_map.py

def load_taxonomy_map(path: str) -> list[TaxonomyEntry]:
    """
    Loads and validates the Taxonomy Map JSON — same fail-fast-at-startup
    pattern as load_company_reference_map() in Agent 1.

    Args:
        path: from config.settings.TAXONOMY_MAP_PATH

    Returns:
        list[TaxonomyEntry]

    Raises:
        ValueError on schema violation, with the offending entry's kpi_id
        in the message.
    """

def get_canonical_section_vocabulary(taxonomy: list[TaxonomyEntry]) -> list[str]:
    """
    Returns the deduplicated union of every `canonical_sections` entry
    across the whole taxonomy — this is the controlled vocabulary Agent 2
    aligns raw section headers against.

    Args:
        taxonomy: output of load_taxonomy_map()

    Returns:
        list[str], deduplicated, order doesn't matter
    """

def get_industry_vocabulary(taxonomy: list[TaxonomyEntry]) -> list[str]:
    """
    Same pattern, for Agent 1's industry fallback — retrofit Agent 1 to
    call this instead of whatever ad hoc list it currently passes as
    `valid_industries`, now that this loader exists.
    """
```

Both Agent 1 and Agent 2 should load the Taxonomy Map **once at process startup**, not per-document — same principle as the company reference map.

---

## 1. What Agent 2 actually does

Three jobs, in order: split the document into sections, classify each section's raw header against the Taxonomy Map's canonical vocabulary, and slot Agent 1b's tables/charts/footnotes into the section they belong to by page.

**Important dependency note**: Agent 1b (table/chart/footnote extraction) hasn't been built yet. Agent 2 should be built and tested against **synthetic `TableElement`/`ChartElement`/`FootnoteElement` fixtures** matching the `common/schemas.py` contracts exactly — don't wait for Agent 1b to exist, and don't build a placeholder extraction step inside Agent 2 to compensate. The interface is the contract; build to it.

---

## 2. Section Splitting

This should consume the **raw `DoclingDocument`** passed through from Agent 1 — not re-parse the flattened `narrative_markdown` string with regex. Docling's document model already carries heading hierarchy and page numbers as structured data; re-deriving that from flat markdown would be throwing away information Agent 1 already has.

```python
# splitter.py

class RawSection(BaseModel):
    section_name_raw: str
    content_markdown: str
    page_range: tuple[int, int]

def split_into_raw_sections(docling_document: object) -> list[RawSection]:
    """
    Walks the DoclingDocument's structure, grouping content under each
    top-level heading into a RawSection. Use the heading level that
    corresponds to major report sections (typically H1/H2 in a financial
    report's structure — e.g. "Consolidated Income Statement" as a
    heading, with line items as body text underneath, not as their own
    headings) — confirm the right heading-level cutoff against a real
    sample document, since report layouts vary.

    Args:
        docling_document: the object Agent 1's converter.py passed through
                          (DoclingConversionResult.docling_document)

    Returns:
        list[RawSection], in document order, page_range derived from the
        first and last page any content under that heading appears on.

    Implementation note: check the installed Docling version's API for the
    exact attribute/method names to walk headings and page provenance —
    this is the one piece of this spec that depends on Docling's actual
    object model rather than a stable, version-independent interface, so
    verify against the installed library's docs rather than assuming.
    """
```

---

## 3. Section Alignment

Maps each `RawSection.section_name_raw` to a single canonical name from the Taxonomy Map's vocabulary, or `"OTHER"` if the section has no KPI relevance (e.g. Chairman's Letter, ESG narrative, Corporate Governance commentary).

### 3.1 First attempt — `align_section()`

```python
class SectionAlignmentResult(BaseModel):
    section_name_canonical: str   # one of the vocabulary entries, or "OTHER"
    confidence: float
    source: Literal["fuzzy_match", "llm_fallback"]

def align_section(
    section_name_raw: str,
    canonical_vocabulary: list[str],
    fuzzy_cutoff: float,
) -> SectionAlignmentResult:
    """
    Stage A: fuzzy-match section_name_raw against canonical_vocabulary
    using rapidfuzz (same library already used for company-name matching
    in Agent 1 — reuse it, don't introduce a second fuzzy-matching
    dependency). Use rapidfuzz.process.extractOne with WRatio, same
    rationale as Agent 1's company lookup.

    If the best match clears fuzzy_cutoff (0-100 scale, from
    config.settings.SECTION_ALIGNMENT_FUZZY_CUTOFF):
        return SectionAlignmentResult(confidence=score/100, source="fuzzy_match")

    Stage B: if no match clears the cutoff, fall back to a GPT-4o
    structured-output call. Build the Literal type for the output schema
    from canonical_vocabulary + ["OTHER"], so the model can only choose a
    valid canonical name or explicitly say this section isn't KPI-relevant.
    Pass section_name_raw plus a short excerpt of the section's own content
    (not just the header) as context, since headers alone are sometimes
    ambiguous (e.g. "Other Information" could be anything).

    Returns:
        SectionAlignmentResult, confidence = the model's self-reported
        confidence field, source="llm_fallback"
    """
```

### 3.2 Retry attempt — `realign_section_low_confidence()`

This is **not** a budget-tracking function — the shared 2-turn retry budget is tracked at the Orchestrator level (per the main spec), not inside this agent. This function is just "what a retry attempt looks like" — the Orchestrator decides whether/when to call it.

```python
def realign_section_low_confidence(
    section_name_raw: str,
    raw_section_content_excerpt: str,
    canonical_vocabulary: list[str],
    previous_result: SectionAlignmentResult,
) -> SectionAlignmentResult:
    """
    Called by the Orchestrator when align_section()'s result fell below
    config.settings.SECTION_ALIGNMENT_CONFIDENCE_THRESHOLD and retry budget
    remains. Always uses the LLM path (no point retrying fuzzy-match the
    same way twice) but with more context than the first attempt: pass a
    larger excerpt of the section's actual content, not just the header,
    and tell the model what the previous low-confidence guess was so it
    can either confirm or override it with better grounding.

    Args:
        section_name_raw, canonical_vocabulary: same as align_section()
        raw_section_content_excerpt: more text than Stage B used the first
                                      time — e.g. first 1000 chars of the
                                      section body, not just the heading
        previous_result: what align_section() returned, for the prompt to
                          reference ("a prior pass classified this as X
                          with low confidence — re-examine and confirm or
                          correct")

    Returns:
        SectionAlignmentResult, source="llm_fallback"

    Caller behavior (Orchestrator, not this function): if confidence is
    still below threshold after this call and no retry budget remains,
    proceed with this result anyway but mark the eventual Section's
    alignment_source as "best_guess_unresolved" rather than "llm_fallback"
    — see common/schemas.py's Section model.
    """
```

---

## 4. Element Assignment

Slots each table/chart/footnote into the section whose page range contains it.

```python
# element_assignment.py

def assign_elements_to_sections(
    sections: list[RawSection],            # already alignment-classified
    alignments: list[SectionAlignmentResult],  # same order as `sections`
    tables: list[TableElement],
    charts: list[ChartElement],
    footnotes: list[FootnoteElement],
) -> list[Section]:
    """
    For each table/chart/footnote, find the section whose page_range
    contains element.page, and append it to that section's `tables` /
    `charts` / `footnotes` list respectively.

    Args:
        sections: output of split_into_raw_sections()
        alignments: one SectionAlignmentResult per section, same index
                    order — produced by align_section() /
                    realign_section_low_confidence() upstream
        tables, charts, footnotes: from Agent 1b (or synthetic fixtures
                                   while Agent 1b doesn't exist yet)

    Returns:
        list[Section] — fully assembled, matching common/schemas.py's
        Section contract exactly (section_name_raw, section_name_canonical,
        alignment_confidence, alignment_source, content_markdown, tables,
        charts, footnotes, page_range)

    Fallback for unmatched pages: if an element's page falls in a gap
    between sections (e.g. a cover page, or a blank page before the first
    detected heading), assign it to the section with the closest page_range
    (nearest preceding section by default — most appendix/cover material
    logically belongs to whatever section precedes it in reading order).
    If no sections exist at all (shouldn't happen in practice, but guard
    against it), log a warning and skip rather than raising — a missing
    table assignment shouldn't crash the whole agent.
    """
```

---

## 5. Configuration additions — `config.py`

```python
class Settings(BaseSettings):
    # ... existing Agent 1 settings ...
    TAXONOMY_MAP_PATH: str
    SECTION_ALIGNMENT_FUZZY_CUTOFF: float = 85.0          # rapidfuzz 0-100 scale, Stage A acceptance
    SECTION_ALIGNMENT_CONFIDENCE_THRESHOLD: float = 0.25   # final confidence bar — separate knob from
                                                            # Agent 1's CLASSIFICATION_CONFIDENCE_THRESHOLD,
                                                            # even though they share the same default value
```

---

## 6. Top-level orchestration — `pipeline.py`

```python
class Agent2Output(BaseModel):
    sections: list[Section]
    retry_turns_used: int   # reported back to the Orchestrator, which owns the actual shared-budget bookkeeping

async def run_agent2(
    docling_document: object,
    report_metadata: ReportMetadata,
    tables: list[TableElement],
    charts: list[ChartElement],
    footnotes: list[FootnoteElement],
    canonical_vocabulary: list[str],
    remaining_retry_budget: int,
) -> Agent2Output:
    """
    Args:
        docling_document: passed through from Agent 1
        report_metadata: not used for branching logic in this agent
                          currently, but accepted for consistency with
                          other agents and in case fiscal_year/report_type-
                          specific section-naming conventions become
                          relevant later
        tables, charts, footnotes: from Agent 1b (or fixtures)
        canonical_vocabulary: get_canonical_section_vocabulary() output,
                              loaded once at startup, passed in here
        remaining_retry_budget: how many of the report's shared 2 turns
                                are left — passed in by the Orchestrator,
                                NOT tracked internally by this agent

    Steps:
        1. raw_sections = split_into_raw_sections(docling_document)
        2. For each raw section: result = align_section(raw_name,
           canonical_vocabulary, fuzzy_cutoff)
        3. For any result below SECTION_ALIGNMENT_CONFIDENCE_THRESHOLD,
           AND remaining_retry_budget > 0: call
           realign_section_low_confidence(...), decrement a local counter,
           and use the improved result if its confidence is higher
        4. For any still below threshold after exhausting budget: keep the
           best available result, but set alignment_source =
           "best_guess_unresolved" on the final Section (not whatever
           Stage A/B originally reported)
        5. sections = assign_elements_to_sections(raw_sections, alignment_
           results, tables, charts, footnotes)

    Returns:
        Agent2Output(sections=sections, retry_turns_used=<however many
        retries were actually consumed>) — the Orchestrator subtracts this
        from the report's shared budget before any subsequent agent (e.g.
        the Validator/Retry Controller loop later) gets to spend from it.
    """
```

---

## 7. Module structure

```
common/
  schemas.py          # ReportMetadata, TaxonomyEntry, TableElement, ChartElement,
                       #   FootnoteElement, Section — moved here from Agent 1 (see §0)
  taxonomy_map.py      # load_taxonomy_map(), get_canonical_section_vocabulary(),
                       #   get_industry_vocabulary() — §0

agent2_section_parser/
  __init__.py
  config.py            # §5 additions on top of shared Settings
  splitter.py           # §2
  alignment.py           # §3 — align_section(), realign_section_low_confidence()
  element_assignment.py  # §4
  pipeline.py            # §6
tests/
  fixtures/
    synthetic_tables.json      # synthetic TableElement list — see note below
    synthetic_charts.json
    synthetic_footnotes.json
  test_splitter.py
  test_alignment.py
  test_element_assignment.py
  test_pipeline.py
```

---

## 8. Synthetic data note

Since Agent 1b doesn't exist yet, you'll need synthetic `TableElement`/`ChartElement`/`FootnoteElement` fixtures to test `assign_elements_to_sections()` — a handful of each, with `page` values deliberately chosen to land inside a section's range, at a boundary, and in a gap between sections (to test the nearest-section fallback). Also worth a small set of raw section header strings for `align_section()` covering: an exact/near-exact match to a canonical name, a worded-very-differently-but-semantically-equivalent header (tests whether fuzzy-match correctly falls through to the LLM stage rather than false-matching), and a genuinely non-KPI section header (should resolve to `"OTHER"`). Happy to generate a full synthetic-data doc for this agent the same way I did for Agent 1, if useful before you start testing.

---

## 9. Open items

- **Docling heading-level cutoff**: which heading level corresponds to "a section" needs verifying against a real sample document — financial report structure varies (some use H1 for top-level statements, others nest under a generic "Financial Statements" H1 with H2 subsections that are the real boundaries you want).
- **`"OTHER"` sections and downstream agents**: confirm Agent 3 (Persistence) still persists `OTHER`-classified sections in full (their tables/footnotes might still matter even if the section itself isn't KPI-relevant) — nothing in the current spec excludes them, but worth confirming that's the intended behavior rather than an oversight.
- **Nearest-section fallback for unmatched-page elements**: "nearest preceding section" is the proposed default in §4 — confirm this is preferable to "nearest by absolute page distance" (which could sometimes mean a *following* section), depending on how your source documents are typically laid out.
