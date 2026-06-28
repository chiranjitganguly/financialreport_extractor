"""Validation Rules Map loader.

Same fail-fast-on-startup pattern as the Taxonomy Map and company reference
map loaders — any entry that fails Pydantic validation raises ValueError with
the offending rule_id so the misconfiguration is caught at startup, not
mid-pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from common.schemas import ValidationRule


def load_validation_rules_map(path: str) -> list[ValidationRule]:
    """Load and validate the Validation Rules JSON at process startup.

    Args:
        path: Filesystem path to the validation rules JSON file.  Sourced from
            common.config.settings.VALIDATION_RULES_MAP_PATH.

    Returns:
        Validated list[ValidationRule].

    Raises:
        ValueError: If the file is not valid JSON, is not a list, or any entry
            fails Pydantic validation (message includes the offending rule_id).
        FileNotFoundError: If the path does not exist.
    """
    raw = Path(path).read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Validation rules map at '{path}' is not valid JSON: {exc}"
        ) from exc

    if isinstance(data, dict) and "validation_rules" in data:
        data = data["validation_rules"]

    if not isinstance(data, list):
        raise ValueError(
            f"Validation rules map at '{path}' must be a JSON array, "
            f"got {type(data).__name__}"
        )

    rules: list[ValidationRule] = []
    for idx, item in enumerate(data):
        try:
            rules.append(ValidationRule.model_validate(item))
        except ValidationError as exc:
            rule_id = (
                item.get("rule_id", "<unknown>") if isinstance(item, dict) else "<unknown>"
            )
            raise ValueError(
                f"Validation rule {idx} (rule_id='{rule_id}') failed validation: {exc}"
            ) from exc

    return rules
