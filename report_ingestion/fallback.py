from typing import Literal, Optional, Union

from pydantic import BaseModel, create_model

from report_ingestion.llm_client import llm
from report_ingestion.prompts import CLASSIFICATION_FALLBACK_PROMPT
from report_ingestion.schemas import (
    AccountingStandardResult,
    ClassificationFallbackResult,
    IndustryResult,
    ReportTypeResult,
)


# ---------------------------------------------------------------------------
# Static inner schemas for the LLM structured-output call.
# These mirror the public Result models but omit the `source` field — the
# caller stamps "llm_fallback" on every result after the fact.
# ---------------------------------------------------------------------------

class _ReportTypeLLMOut(BaseModel):
    report_type: Literal["annual_report", "quarterly_report", "regulatory_filing"]
    confidence: float


class _AccountingStandardLLMOut(BaseModel):
    standard: Literal["IFRS", "US-GAAP", "IND-AS", "OTHER"]
    confidence: float


def _make_industry_literal(valid_industries: list[str]) -> type:
    """Return a Union[Literal[v1], Literal[v2], ...] equivalent to Literal[v1, v2, ...]."""
    if not valid_industries:
        return str  # type: ignore[return-value]
    literal_types = tuple(Literal[v] for v in valid_industries)  # type: ignore[misc]
    return Union[literal_types]  # type: ignore[return-value]


def _build_llm_output_schema(
    needed_fields: list[str],
    valid_industries: list[str],
) -> type[BaseModel]:
    """Dynamically build a Pydantic model containing only the needed fields.

    Each field is Optional so the model is lenient if the LLM omits something,
    and so fields *not* in needed_fields can be safely left as None.
    """
    field_defs: dict[str, tuple] = {}

    if "report_type" in needed_fields:
        field_defs["report_type"] = (Optional[_ReportTypeLLMOut], None)

    if "accounting_standard" in needed_fields:
        field_defs["accounting_standard"] = (Optional[_AccountingStandardLLMOut], None)

    if "industry" in needed_fields:
        IndustryLLMOut = create_model(
            "_IndustryLLMOut",
            industry=(_make_industry_literal(valid_industries), ...),
            confidence=(float, ...),
        )
        field_defs["industry"] = (Optional[IndustryLLMOut], None)  # type: ignore[assignment]

    return create_model("_ClassificationFallbackLLMOut", **field_defs)  # type: ignore[call-overload]


async def run_classification_fallback(
    excerpt: str,
    needed_fields: list[Literal["report_type", "accounting_standard", "industry"]],
    valid_industries: list[str],
) -> ClassificationFallbackResult:
    """Stage C: single batched GPT-4o call for all fields not resolved in Stages A/B.

    Called at most once per document. If ``needed_fields`` is empty this function
    must not be called at all — the caller (pipeline.py) is responsible for the
    guard.

    The Pydantic schema passed to ``with_structured_output()`` is built dynamically
    so the model is only asked about fields that are actually needed; already-resolved
    fields are not re-classified. The prompt instructs the model to leave unrequested
    fields null.

    Args:
        excerpt: Output of get_classification_excerpt(). Widen if
            ``"accounting_standard"`` is in ``needed_fields`` and standard
            statements are known to sit outside the default excerpt window.
        needed_fields: The subset of fields that did not resolve in Stages A/B.
            Determines which sub-schemas are included in the structured-output call.
        valid_industries: Full industry vocabulary from the Taxonomy Map, used to
            build a ``Literal[...]`` type for the industry field dynamically (only
            relevant when ``"industry"`` is in ``needed_fields``).

    Returns:
        ClassificationFallbackResult with ``source="llm_fallback"`` set on every
        populated sub-result and the model's self-reported confidence per field.
        Fields not in ``needed_fields`` are None.
    """
    OutputSchema = _build_llm_output_schema(needed_fields, valid_industries)
    chain = CLASSIFICATION_FALLBACK_PROMPT | llm.with_structured_output(OutputSchema).with_retry()

    raw = await chain.ainvoke(
        {
            "excerpt": excerpt,
            "needed_fields": ", ".join(needed_fields),
            "valid_industries": ", ".join(valid_industries) if valid_industries else "any",
        }
    )

    # Map the dynamic LLM output back to the stable public result types,
    # stamping source="llm_fallback" on every populated field.
    report_type_result: Optional[ReportTypeResult] = None
    if "report_type" in needed_fields and raw.report_type is not None:
        report_type_result = ReportTypeResult(
            report_type=raw.report_type.report_type,
            confidence=raw.report_type.confidence,
            source="llm_fallback",
        )

    accounting_standard_result: Optional[AccountingStandardResult] = None
    if "accounting_standard" in needed_fields and raw.accounting_standard is not None:
        accounting_standard_result = AccountingStandardResult(
            standard=raw.accounting_standard.standard,
            confidence=raw.accounting_standard.confidence,
            source="llm_fallback",
        )

    industry_result: Optional[IndustryResult] = None
    if "industry" in needed_fields and raw.industry is not None:
        industry_result = IndustryResult(
            industry=raw.industry.industry,
            confidence=raw.industry.confidence,
            source="llm_fallback",
        )

    return ClassificationFallbackResult(
        report_type=report_type_result,
        accounting_standard=accounting_standard_result,
        industry=industry_result,
    )
