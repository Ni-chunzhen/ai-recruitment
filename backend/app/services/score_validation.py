from __future__ import annotations

from typing import Any

SCORE_RESULT_SCHEMA_VERSION = "1.0"
SCORE_DIFF_EPSILON = 0.01


class ScoreOutputInvalidError(ValueError):
    """Dify returned a payload that cannot become a formal score report."""


def snapshot_dimensions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    raw = snapshot.get("dimensions") or snapshot.get("dimensions_json") or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("name")]


def validate_score_against_snapshot(
    *,
    normalized: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    expected = [str(item["name"]).strip() for item in snapshot_dimensions(snapshot)]
    actual_items = list(normalized.get("dimensions") or [])
    actual = [
        str(item.get("name") or "").strip()
        for item in actual_items
        if isinstance(item, dict)
    ]
    if not expected:
        raise ScoreOutputInvalidError("input snapshot has no score dimensions")
    if len(actual) != len(set(actual)):
        raise ScoreOutputInvalidError("duplicate dimension names in AI output")
    expected_set = set(expected)
    actual_set = set(actual)
    unknown = actual_set - expected_set
    missing = expected_set - actual_set
    if unknown or missing:
        raise ScoreOutputInvalidError(
            "dimension name mismatch: "
            f"unknown={sorted(unknown)} missing={sorted(missing)}"
        )
    if len(actual) != len(expected):
        raise ScoreOutputInvalidError(
            f"dimension count mismatch: expected {len(expected)}, got {len(actual)}"
        )


def order_dimensions_by_snapshot(
    dimensions: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    by_name = {
        str(item.get("name") or "").strip(): item
        for item in dimensions
        if isinstance(item, dict)
    }
    ordered: list[dict[str, Any]] = []
    for item in snapshot_dimensions(snapshot):
        name = str(item["name"]).strip()
        if name in by_name:
            ordered.append(by_name[name])
    return ordered


def compute_score_totals(
    *,
    dimensions: list[dict[str, Any]],
    weight_map: dict[str, float],
    model_total: float | None,
) -> tuple[list[dict[str, Any]], float, float | None, list[str]]:
    recomputed: list[dict[str, Any]] = []
    total = 0.0
    for item in dimensions:
        name = str(item.get("name") or "")
        score = float(item.get("score") or 0)
        weight = float(weight_map.get(name, item.get("weight") or 0))
        weighted = round(score * (weight / 100.0), 2)
        recomputed.append(
            {
                **item,
                "weight": weight,
                "score": score,
                "weighted_score": weighted,
            }
        )
        total += weighted
    calculated = round(total, 2)
    warnings: list[str] = []
    difference: float | None = None
    if model_total is not None:
        difference = round(float(model_total) - calculated, 4)
        if abs(difference) > SCORE_DIFF_EPSILON:
            warnings.append(
                f"model_total_score {model_total} differs from "
                f"calculated_total_score {calculated} by {difference}"
            )
    return recomputed, calculated, difference, warnings


def validate_screening_payload(
    *,
    decision: str,
    reason_code: str | None,
    reason: str | None,
    required_decisions: set[str],
    allowed_codes: set[str],
    other_code: str,
) -> None:
    code = (reason_code or "").strip() or None
    text = (reason or "").strip() or None
    if decision in required_decisions and not code:
        raise ValueError("reason_code is required for this decision")
    if code and code not in allowed_codes:
        raise ValueError("invalid reason_code")
    if code == other_code and not text:
        raise ValueError("reason is required when reason_code is other")
