# Agents 7, 8, 9: Validator, Retry Controller, Consolidation — Implementation Design

**Status**: Ready for code generation, with one scope limitation flagged below rather than silently built around.

---

## 0. Shared infrastructure and one scope limitation

### 0.1 Validation Rules Map — schema and loader (new)

This has only ever been described conceptually ("externally-supplied config") — it needs an actual schema before Agent 7 can consume it. You mentioned the rule content will be framed and provided separately; this defines the *format* that content needs to arrive in.

```python
# common/schemas.py — add

class ValidationRule(BaseModel):
    rule_id: str
    description: str
    rule_type: Literal["tally", "plausibility_bound"]
    formula: str            # an expression using kpi_id strings as variable names,
                              # evaluated via simpleeval (see below) — e.g.
                              # "abs(net_income - (revenue - expenses)) <= tolerance"
                              # or, for a bound: "0 <= expense_ratio <= 100"
    participating_kpi_ids: list[str]
    tolerance: float = 0.0   # only meaningful for tally-type rules; the formula
                              # itself can reference `tolerance` as a variable
                              # if you want it folded into the expression directly
```

**Why `simpleeval` (MIT) and not raw Python `eval()`**: rule formulas are externally-supplied data, not code you wrote — evaluating arbitrary strings with `eval()` against external input is a real security exposure. `simpleeval` sandboxes expression evaluation to arithmetic/comparison operations only, which is exactly what a tally or bound check needs and nothing more.

**Scope limitation — `plausibility_bound` rules cannot do period-over-period comparison.** The spec's example ("period-over-period plausibility bounds") implies comparing this period's value to a *prior* period's — but `ExtractionLedger` is scoped to a single report run; there's no mechanism anywhere in the pipeline to fetch a previous filing's extracted KPIs. Build `plausibility_bound` to support **within-report bounds only** (e.g. "an expense ratio must be between 0 and 100") for v1. Period-over-period checks need a historical KPI store across report runs, which doesn't exist — that's a real architectural addition for later, not something to fake now.

```python
# common/validation_rules.py

def load_validation_rules_map(path: str) -> list[ValidationRule]:
    """Same fail-fast-at-startup pattern as the Taxonomy Map and company
    reference map loaders. Raises ValueError with the offending rule_id
    on schema violation."""
```

### 0.2 Targeted single-KPI retry functions — retrofit Agent 6

The Retry Controller needs to re-run extraction for *one specific KPI*, not a full batched run. Tier 1 and Tier 2 already have single-KPI functions (`extract_tier1_candidates_for_kpi`, `extract_tier2_candidates_for_kpi` from the Agent 4/5/6 design) — reuse them directly. Tier 3 only has a per-section batched function; add a single-KPI variant:

```python
# agent6_tier3/extraction.py — add

def run_tier3_retry_for_kpi(
    section: Section,
    kpi_request: Tier3KPIRequest,
    validator_note: Optional[str] = None,
) -> Tier3ExtractionResult:
    """
    Single-KPI, non-batched GPT-4o call — used only by the Retry Controller,
    never by the normal cascade (which always batches per section).

    Args:
        section, kpi_request: same as the batched function, just one KPI
        validator_note: when retrying because a tally check failed, fold
                         the specific discrepancy into the prompt (e.g.
                         "A prior extraction gave revenue=X, but Net Income
                         = Revenue - Expenses didn't reconcile — re-examine
                         this section for the correct figure"). When
                         retrying a never-found KPI instead, pass a note
                         like "Not found in a prior pass — check the full
                         section carefully, including any chart or footnote
                         content" rather than a discrepancy-specific note.

    Returns:
        Tier3ExtractionResult
    """
```

---

## 1. Agent 7 — Validator

Split into two phases, not one: **tally checks run every retry turn** (since a retry might fix them), while **low-confidence pass-through and footnote materiality are final, single-pass checks** run once after the retry loop converges (since re-running them every turn on records that haven't changed would be pointless, and running them before the loop converges risks marking something `needs_human_review` for low confidence right before a retry would have fixed it).

### 1.1 Tally checks — `agent7_validator/tally.py` (runs every turn)

```python
class RuleEvaluationResult(BaseModel):
    rule_id: str
    skipped: bool          # True if any participating KPI has no value yet
    passed: Optional[bool] # None if skipped
    participating_kpi_ids: list[str]

def evaluate_rule(rule: ValidationRule, ledger: ExtractionLedger) -> RuleEvaluationResult:
    """
    Gathers each participating_kpi_id's current value from the ledger.
    If ANY participant has status in ("not_found",) or a non-numeric
    value: skipped=True, passed=None — don't fail a rule because an input
    is simply missing, that's a different problem (the KPI itself will
    surface as not_found_after_retries on its own).

    Otherwise: result = simpleeval.simple_eval(rule.formula, names={kpi_id: value, ...,
    "tolerance": rule.tolerance}). For a tally rule, the formula should
    itself resolve to a boolean (e.g. "abs(a - (b - c)) <= tolerance") —
    don't build separate tolerance-comparison logic outside the expression;
    keep the pass/fail boolean entirely inside the evaluated formula so
    plausibility_bound and tally rules share one evaluation path.

    Returns:
        RuleEvaluationResult
    """

def run_tally_checks(ledger: ExtractionLedger, rules: list[ValidationRule]) -> tuple[ExtractionLedger, list[str]]:
    """
    For each rule, evaluate_rule(). On a failure (not skipped, passed=False):
    for each participating_kpi_id:
        - if that record's status is currently "needs_human_review" with
          review_reason in ("section_discrepancy", "footnoted_caveat") —
          these are TERMINAL per the main spec and must never re-enter the
          retry loop. Append an AttemptRecord noting the rule failure for
          visibility, but do NOT change its status.
        - otherwise: set status="flagged", append an AttemptRecord noting
          which rule failed and the formula's evaluated result.

    Returns:
        (updated ledger, list of kpi_ids actually flagged this call —
        excluding any terminal records that were noted but not flagged)
    """
```

### 1.2 Final review passes — `agent7_validator/final_review.py` (runs once, after the retry loop ends)

```python
def run_low_confidence_passthrough(ledger: ExtractionLedger, threshold: float) -> ExtractionLedger:
    """
    For every record with status == "found" (specifically NOT records
    already needs_human_review for another reason) and confidence < threshold:
    set status="needs_human_review", review_reason="low_confidence".
    """

def run_footnote_materiality_check(
    ledger: ExtractionLedger,
    footnotes_by_id: dict[str, FootnoteElement],
    material_keywords: list[str],
) -> ExtractionLedger:
    """
    For every record with status == "found" (again, not already flagged
    for another reason — a record already needs_human_review for
    low_confidence doesn't also need a footnoted_caveat reason layered on
    top; one review reason is enough context) and a non-empty `footnotes`
    list: look up each footnote_id's text via footnotes_by_id, check
    classify_footnote_materiality(). If material: status="needs_human_review",
    review_reason="footnoted_caveat".

    Note this runs AFTER run_low_confidence_passthrough() in the calling
    sequence — order matters slightly here only in that both checks read
    status=="found", so whichever runs second will see fewer "found"
    records (whatever the first pass already flipped). This is fine and
    intentional — a record doesn't need two review reasons.
    """

def classify_footnote_materiality(footnote_texts: list[str], material_keywords: list[str]) -> bool:
    """
    v1 keyword heuristic (per the main spec's resolved decision — LLM
    judgment is the documented future upgrade, not built now): case-
    insensitive substring search across all footnote_texts. True if any
    keyword from material_keywords appears in any footnote text.
    """
```

---

## 2. Agent 8 — Retry Controller

```python
# agent8_retry_controller/routing.py

def determine_retry_tier(record: ExtractionRecord) -> Literal["tier1_to_tier2", "tier2_to_tier3", "tier3_recheck", "tier3_broader"]:
    """
    Routing table (this resolves an ambiguity in the main spec's "Tier 1
    flagged -> retry via Tier 2 or Tier 3" wording — interpreting it as
    "escalate one tier up," confirm this matches your intent):

    - record.method == "deterministic" (came from Tier 1) and status == "flagged"
        -> "tier1_to_tier2"
    - record.method == "semantic" (came from Tier 2) and status == "flagged"
        -> "tier2_to_tier3"
    - record.method == "llm" (came from Tier 3) and status == "flagged"
        -> "tier3_recheck"   (re-run Tier 3 with the validator's specific
                               discrepancy folded into the prompt)
    - record.status == "not_found" (never resolved by any tier)
        -> "tier3_broader"   (re-run Tier 3 with broader context, not a
                               narrow discrepancy note, since there's no
                               specific discrepancy to describe — just
                               "this wasn't found, look harder")
    """

# agent8_retry_controller/retry.py

def retry_kpi(
    record: ExtractionRecord,
    taxonomy_entry: TaxonomyEntry,
    sections_by_canonical_name: dict[str, list[Section]],
    report_id: str,
    fiscal_year: str,
) -> ExtractionRecord:
    """
    Dispatches on determine_retry_tier():
        "tier1_to_tier2" -> extract_tier2_candidates_for_kpi(taxonomy_entry, report_id, fiscal_year, top_k)
        "tier2_to_tier3" -> run_tier3_retry_for_kpi(relevant section, kpi_request, validator_note=last attempt's note)
        "tier3_recheck"  -> run_tier3_retry_for_kpi(..., validator_note=<the specific tally failure note from record.attempts[-1]>)
        "tier3_broader"  -> run_tier3_retry_for_kpi(..., validator_note="not found in prior passes — examine thoroughly")

    Whatever candidate(s) come back: if exactly one distinct value, update
    the record (status="found", new method/confidence/etc, append an
    AttemptRecord for this retry). If still nothing or still disagreeing
    with itself across sections, leave status as "flagged"/"not_found" and
    still append an AttemptRecord noting the retry didn't resolve it.

    Returns:
        updated ExtractionRecord (NOT yet ledger-level — caller assembles)
    """

def run_retry_turn(
    ledger: ExtractionLedger,
    flagged_kpi_ids: list[str],
    taxonomy_by_id: dict[str, TaxonomyEntry],
    sections_by_canonical_name: dict[str, list[Section]],
    report_id: str,
    fiscal_year: str,
) -> ExtractionLedger:
    """
    Calls retry_kpi() for every id in flagged_kpi_ids (all together, per
    the main spec — "each turn re-invokes extraction for all currently-
    flagged KPIs together"), updates the ledger in place, returns it.
    """
```

---

## 3. The Validator ⇄ Retry Controller loop — top-level orchestration

Spans Agents 7 and 8, same reasoning as the extraction cascade spanning Agents 4-6.

```python
# validation_retry_loop.py

class ValidationRetryOutput(BaseModel):
    ledger: ExtractionLedger
    turns_used: int

def run_validation_retry_loop(
    ledger: ExtractionLedger,
    rules: list[ValidationRule],
    taxonomy_by_id: dict[str, TaxonomyEntry],
    sections_by_canonical_name: dict[str, list[Section]],
    footnotes_by_id: dict[str, FootnoteElement],
    report_id: str,
    fiscal_year: str,
    remaining_retry_budget: int,
    confidence_threshold: float,
    material_keywords: list[str],
) -> ValidationRetryOutput:
    """
    Args:
        remaining_retry_budget: turns left in the report's SHARED 2-turn
                                  budget — this is NOT a fresh counter;
                                  it's whatever's left after Agent 2's
                                  section-alignment retries already spent
                                  some. Threaded in from whatever calls
                                  this (the not-yet-built Orchestrator).

    Steps:
        turns_used = 0
        loop:
            ledger, flagged_kpi_ids = run_tally_checks(ledger, rules)
            if not flagged_kpi_ids or turns_used >= remaining_retry_budget:
                break
            ledger = run_retry_turn(ledger, flagged_kpi_ids, ...)
            turns_used += 1

        # after the loop: anything still flagged, or still not_found,
        # becomes terminal needs_human_review
        for each record still status == "flagged":
            status = "needs_human_review", review_reason = "validation_failed"
        for each record still status == "not_found":
            status = "needs_human_review", review_reason = "not_found_after_retries"

        # final single-pass checks, only on records that are genuinely "found"
        ledger = run_low_confidence_passthrough(ledger, confidence_threshold)
        ledger = run_footnote_materiality_check(ledger, footnotes_by_id, material_keywords)

    Returns:
        ValidationRetryOutput(ledger=ledger, turns_used=turns_used) —
        turns_used is reported back to the Orchestrator for bookkeeping
        consistency with how Agent 2 reports its own usage.
    """
```

---

## 4. Agent 9 — Consolidation

```python
# agent9_consolidation/pipeline.py

class ResolvedKPIOutput(BaseModel):
    kpi: str
    value: str | float
    section: Optional[str]
    page: Optional[int]
    method: str
    source_element_type: Optional[str]
    confidence: float
    footnotes: list[str]

class NeedsReviewKPIOutput(BaseModel):
    kpi: str
    value: Optional[str | float]
    section: Optional[str]
    page: Optional[int]
    source_element_type: Optional[str]
    confidence: float
    review_reason: str
    footnotes: list[str]
    conflicting_values: list[ConflictingValue]
    attempts: list[AttemptRecord]

class FinalReportOutput(BaseModel):
    report_id: str
    fiscal_year: str
    resolved: list[ResolvedKPIOutput]
    needs_review: list[NeedsReviewKPIOutput]

def run_consolidation(ledger: ExtractionLedger, report_id: str, fiscal_year: str) -> FinalReportOutput:
    """
    Partitions by final status:
        status == "found"               -> ResolvedKPIOutput
        status == "needs_human_review"   -> NeedsReviewKPIOutput

    Defensive case: a record with status still "flagged" or "not_found"
    at this point means run_validation_retry_loop() wasn't actually run to
    completion before calling this (a wiring bug upstream, not a normal
    outcome) — log a warning and place it in needs_review with whatever
    review_reason is available, rather than silently dropping it or
    raising and losing the whole report's output.

    Returns:
        FinalReportOutput
    """
```

---

## 5. Configuration additions

```python
class Settings(BaseSettings):
    # ... existing settings ...
    VALIDATION_RULES_MAP_PATH: str
    EXTRACTION_CONFIDENCE_THRESHOLD: float = 0.25
    FOOTNOTE_MATERIALITY_KEYWORDS: list[str] = [
        "adjusted", "excludes", "excluding", "restated", "non-gaap",
        "pro forma", "one-time", "exceptional item", "discontinued operations",
    ]
```

---

## 6. Module structure

```
common/
  schemas.py              # + ValidationRule
  validation_rules.py       # §0.1 — load_validation_rules_map()

agent7_validator/
  __init__.py
  tally.py                  # §1.1
  final_review.py             # §1.2

agent8_retry_controller/
  __init__.py
  routing.py                  # §2
  retry.py                      # §2

validation_retry_loop.py        # §3 — spans Agents 7 and 8

agent9_consolidation/
  __init__.py
  pipeline.py                    # §4

tests/
  fixtures/
    sample_validation_rules.json  # synthetic — at least one tally rule with
                                   #   exactly-balancing values, one deliberately
                                   #   unbalanced, and one with a missing operand
                                   #   (to test the "skipped" path)
  test_tally.py
  test_final_review.py              # footnote keyword matching, confidence passthrough
  test_retry_routing.py               # determine_retry_tier() branch coverage
  test_validation_retry_loop.py         # full loop, mocked tier calls, verify turn budget respected
  test_consolidation.py
```

---

## 7. Open items

- **Retry routing interpretation**: §2's "escalate one tier up" reading of the spec's "Tier 1 flagged -> retry via Tier 2 or Tier 3" is an interpretation, not an explicit spec instruction — confirm this is the intended behavior, since the alternative (always jump straight to Tier 3 regardless of which tier originally produced the flagged value) is also a defensible reading and would be a different, simpler routing table.
- **`plausibility_bound` period-over-period limitation (§0.1)**: flagged as out of scope for v1 — if cross-period validation turns out to matter soon, that's a separate architectural addition (a historical KPI store), not a tweak to this agent.
- **Footnote materiality keyword list**: the defaults in §5 are a reasonable starting set, not validated against real footnote language in your target documents — expect to tune.
- **Two terminal review reasons colliding on one record**: §1.2 notes that a record already `needs_human_review` for one reason doesn't get a second reason layered on. If you'd rather see *all* applicable reasons on a record (e.g. both low-confidence AND a material footnote), that's a small change to `review_reason` becoming a list instead of a single value — worth deciding now since it's a contract change, not a quick tweak.
