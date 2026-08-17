"""Deterministic interview AI validation: keys, weights, questions and evidence."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

from pydantic import ValidationError

from app.schemas.interview_ai import (
    InterviewDimensionSnapshot,
    InterviewEvidenceSegment,
    InterviewQuestionGenerateResult,
    InterviewRoundAnalyzeResult,
)

_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)
_QUESTION_SPACE_RE = re.compile(r"\s+")
_WEIGHT_TOLERANCE = Decimal("0.01")
_SCORE_QUANTUM = Decimal("0.01")


class AIOutputValidationError(ValueError):
    """Safe validation failure: stable code, no original payload values."""

    def __init__(self, message: str, *, code: str = "output_validation_failed") -> None:
        super().__init__(message)
        self.code = code


def raise_safe_validation_error(exc: ValidationError) -> None:
    parts: list[str] = []
    for err in exc.errors(include_url=False, include_input=False):
        loc = ".".join(str(item) for item in err.get("loc", ()))
        msg = str(err.get("msg") or "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    raise AIOutputValidationError(
        "; ".join(parts) or "invalid AI output",
        code="output_validation_failed",
    ) from None


def allocate_dimension_key(position: int) -> str:
    if isinstance(position, bool) or not isinstance(position, int):
        raise AIOutputValidationError(
            "dimension position must be an integer 1-999",
            code="invalid_dimension_key",
        )
    if position < 1 or position > 999:
        raise AIOutputValidationError(
            "dimension position must be 1-999",
            code="invalid_dimension_key",
        )
    return f"D{position:03d}"


def _as_decimal(value: object, *, code: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise AIOutputValidationError("invalid numeric value", code=code)
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise AIOutputValidationError("invalid numeric value", code=code)
            number = Decimal(str(value))
        elif isinstance(value, str):
            number = Decimal(value.strip())
        else:
            raise AIOutputValidationError("invalid numeric value", code=code)
    except (InvalidOperation, ValueError) as exc:
        raise AIOutputValidationError("invalid numeric value", code=code) from exc
    if not number.is_finite():
        raise AIOutputValidationError("invalid numeric value", code=code)
    return number


def build_dimension_snapshot(
    score_dimensions: Sequence[Mapping[str, object]],
) -> list[InterviewDimensionSnapshot]:
    if not score_dimensions:
        raise AIOutputValidationError(
            "score dimensions must not be empty",
            code="invalid_dimension_snapshot",
        )
    if len(score_dimensions) > 50:
        raise AIOutputValidationError(
            "at most 50 dimensions are allowed",
            code="invalid_dimension_snapshot",
        )
    snapshot: list[InterviewDimensionSnapshot] = []
    for index, raw in enumerate(score_dimensions, start=1):
        if not isinstance(raw, Mapping):
            raise AIOutputValidationError(
                "dimension item must be an object",
                code="invalid_dimension_snapshot",
            )
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AIOutputValidationError(
                "dimension name must be non-empty",
                code="invalid_dimension_snapshot",
            )
        description = raw.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise AIOutputValidationError(
                "dimension description must be a string",
                code="invalid_dimension_snapshot",
            )
        anchors_raw = raw.get("anchors", [])
        if anchors_raw is None:
            anchors_raw = []
        if not isinstance(anchors_raw, list) or any(
            not isinstance(item, str) for item in anchors_raw
        ):
            raise AIOutputValidationError(
                "anchors must be a list of strings",
                code="invalid_dimension_snapshot",
            )
        snapshot.append(
            InterviewDimensionSnapshot(
                dimension_key=allocate_dimension_key(index),
                display_order=index,
                name=name.strip(),
                weight=_as_decimal(
                    raw.get("weight"), code="invalid_dimension_snapshot"
                ),
                description=description.strip(),
                anchors=[item.strip() for item in anchors_raw],
            )
        )
    return snapshot


def require_complete_analysis_anchors(
    dimensions: Sequence[InterviewDimensionSnapshot],
) -> None:
    for item in dimensions:
        trimmed = [anchor.strip() for anchor in item.anchors]
        if len(trimmed) != 5 or any(not anchor for anchor in trimmed):
            raise AIOutputValidationError(
                f"{item.dimension_key} ({item.name}) must have exactly 5 non-empty anchors",
                code="incomplete_anchors",
            )
        if len(set(trimmed)) != 5:
            raise AIOutputValidationError(
                f"{item.dimension_key} ({item.name}) has duplicate anchors",
                code="incomplete_anchors",
            )


def validate_dimension_weights(
    dimensions: Sequence[InterviewDimensionSnapshot],
) -> None:
    total = Decimal("0")
    for item in dimensions:
        weight = item.weight
        if not weight.is_finite() or weight <= 0 or weight > Decimal("100"):
            raise AIOutputValidationError(
                f"{item.dimension_key}: weight must be in (0, 100]",
                code="invalid_dimension_weights",
            )
        total += weight
    if abs(total - Decimal("100")) > _WEIGHT_TOLERANCE:
        raise AIOutputValidationError(
            "dimension weights must sum to 100 ± 0.01",
            code="invalid_dimension_weights",
        )


def compute_interview_overall_score(
    dimensions: Sequence[InterviewDimensionSnapshot],
    scores_by_dimension_key: Mapping[str, int | None],
) -> Decimal | None:
    validate_dimension_weights(dimensions)
    expected = {item.dimension_key for item in dimensions}
    actual = set(scores_by_dimension_key)
    if expected != actual:
        raise AIOutputValidationError(
            "score keys must match snapshot dimension keys",
            code="invalid_interview_score",
        )
    scores: list[int | None] = []
    for item in dimensions:
        score = scores_by_dimension_key[item.dimension_key]
        if score is None:
            scores.append(None)
            continue
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            raise AIOutputValidationError(
                f"{item.dimension_key}: score must be an integer 1-5",
                code="invalid_interview_score",
            )
        scores.append(score)
    if any(score is None for score in scores):
        return None
    total = Decimal("0")
    for item, score in zip(dimensions, scores, strict=True):
        assert score is not None
        total += Decimal(score) * item.weight / Decimal("100")
    quantized = total.quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)
    if quantized < Decimal("1.00") or quantized > Decimal("5.00"):
        raise AIOutputValidationError(
            "overall score must be between 1.00 and 5.00",
            code="invalid_interview_score",
        )
    return quantized


def _normalize_question_text(value: str) -> str:
    return _QUESTION_SPACE_RE.sub("", value.strip()).casefold()


def validate_question_result_against_snapshot(
    result: InterviewQuestionGenerateResult,
    dimensions: Sequence[InterviewDimensionSnapshot],
) -> None:
    allowed = {item.dimension_key for item in dimensions}
    orders = [item.display_order for item in result.questions]
    expected_orders = list(range(1, len(result.questions) + 1))
    if orders != expected_orders:
        raise AIOutputValidationError(
            "display_order must run consecutively from 1 to N",
            code="invalid_question_result",
        )
    seen_questions: set[str] = set()
    for item in result.questions:
        if item.dimension_key not in allowed:
            raise AIOutputValidationError(
                f"unknown dimension_key {item.dimension_key}",
                code="invalid_question_result",
            )
        evidence = (item.resume_evidence or "").strip()
        if item.evidence_source == "RESUME_EXPERIENCE":
            if not evidence:
                raise AIOutputValidationError(
                    f"{item.dimension_key}: RESUME_EXPERIENCE requires resume_evidence",
                    code="invalid_question_result",
                )
        elif evidence:
            raise AIOutputValidationError(
                f"{item.dimension_key}: resume_evidence must be empty for this source",
                code="invalid_question_result",
            )
        normalized = _normalize_question_text(item.question)
        if normalized in seen_questions:
            raise AIOutputValidationError(
                "duplicate question text is not allowed",
                code="invalid_question_result",
            )
        seen_questions.add(normalized)


def normalize_evidence_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        raise AIOutputValidationError(
            "evidence text must not be empty",
            code="invalid_evidence",
        )
    return text


def _segment_catalog(
    segments: Sequence[InterviewEvidenceSegment],
) -> dict[UUID, InterviewEvidenceSegment]:
    catalog: dict[UUID, InterviewEvidenceSegment] = {}
    for segment in segments:
        if segment.id in catalog:
            raise AIOutputValidationError(
                "segment ids must be unique",
                code="invalid_evidence",
            )
        catalog[segment.id] = segment
    return catalog


def _validate_evidence(
    *,
    dimension_key: str,
    evidence,
    catalog: Mapping[UUID, InterviewEvidenceSegment],
    transcript_version_id: UUID,
) -> None:
    seen_segments: set[UUID] = set()
    for ref in evidence:
        if ref.segment_id in seen_segments:
            raise AIOutputValidationError(
                f"{dimension_key}: duplicate segment_id {ref.segment_id}",
                code="invalid_evidence",
            )
        seen_segments.add(ref.segment_id)
        segment = catalog.get(ref.segment_id)
        if segment is None:
            raise AIOutputValidationError(
                f"{dimension_key}: unknown segment_id {ref.segment_id}",
                code="invalid_evidence",
            )
        if segment.transcript_version_id != transcript_version_id:
            raise AIOutputValidationError(
                f"{dimension_key}: segment_id {ref.segment_id} is outside transcript version",
                code="invalid_evidence",
            )
        if not segment.is_included_in_analysis:
            raise AIOutputValidationError(
                f"{dimension_key}: segment_id {ref.segment_id} is excluded from analysis",
                code="invalid_evidence",
            )
        if segment.segment_no != ref.segment_no:
            raise AIOutputValidationError(
                f"{dimension_key}: segment_no does not match segment_id {ref.segment_id}",
                code="invalid_evidence",
            )
        segment_text = normalize_evidence_text(segment.text)
        quote = normalize_evidence_text(ref.quote)
        if quote not in segment_text:
            raise AIOutputValidationError(
                f"{dimension_key}: quote is not a contiguous substring of segment_id {ref.segment_id}",
                code="invalid_evidence",
            )


def validate_analysis_result_against_snapshot(
    result: InterviewRoundAnalyzeResult,
    dimensions: Sequence[InterviewDimensionSnapshot],
    *,
    transcript_version_id: UUID,
    segments: Sequence[InterviewEvidenceSegment],
) -> Decimal | None:
    snapshot_keys = [item.dimension_key for item in dimensions]
    result_keys = [item.dimension_key for item in result.dimensions]
    if len(result_keys) != len(set(result_keys)):
        raise AIOutputValidationError(
            "analysis dimensions must not repeat dimension_key",
            code="invalid_analysis_result",
        )
    missing = set(snapshot_keys) - set(result_keys)
    unknown = set(result_keys) - set(snapshot_keys)
    if missing or unknown:
        raise AIOutputValidationError(
            "analysis dimensions must match snapshot keys "
            f"missing={sorted(missing)} unknown={sorted(unknown)}",
            code="invalid_analysis_result",
        )
    catalog = _segment_catalog(segments)
    by_key = {item.dimension_key: item for item in result.dimensions}
    scores: dict[str, int | None] = {}
    for snapshot in dimensions:
        item = by_key[snapshot.dimension_key]
        insufficient = (item.insufficient_information or "").strip()
        if item.score is None:
            if not insufficient:
                raise AIOutputValidationError(
                    f"{item.dimension_key}: insufficient_information is required when score is empty",
                    code="invalid_analysis_result",
                )
        else:
            if insufficient:
                raise AIOutputValidationError(
                    f"{item.dimension_key}: insufficient_information must be empty when scored",
                    code="invalid_analysis_result",
                )
            if not item.evidence:
                raise AIOutputValidationError(
                    f"{item.dimension_key}: scored dimensions require evidence",
                    code="invalid_analysis_result",
                )
        _validate_evidence(
            dimension_key=item.dimension_key,
            evidence=item.evidence,
            catalog=catalog,
            transcript_version_id=transcript_version_id,
        )
        scores[item.dimension_key] = item.score
    return compute_interview_overall_score(dimensions, scores)
