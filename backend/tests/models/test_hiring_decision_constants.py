"""HiringDecision constants and immutable model shape (Task 1)."""

from __future__ import annotations

from app.models.resume import (
    HIRING_DECISIONS,
    HIRING_HOLD,
    HIRING_REASON_CODES,
    HIRING_RECOMMEND_HIRE,
    HIRING_REJECT,
    PIPELINE_PENDING_OFFER,
    PIPELINE_STATUSES,
    HiringDecision,
    list_hiring_reason_catalog,
)


def test_pipeline_statuses_include_pending_offer() -> None:
    assert PIPELINE_PENDING_OFFER == "pending_offer"
    assert "pending_offer" in PIPELINE_STATUSES
    assert len(PIPELINE_STATUSES) == 6
    assert PIPELINE_STATUSES == frozenset(
        {
            "pending_parse",
            "pending_hr_screen",
            "interviewing",
            "pending_offer",
            "rejected",
            "talent_pool",
        }
    )


def test_hiring_decisions_exactly_three() -> None:
    assert HIRING_DECISIONS == frozenset(
        {HIRING_RECOMMEND_HIRE, HIRING_REJECT, HIRING_HOLD}
    )
    assert HIRING_DECISIONS == frozenset(
        {"recommend_hire", "reject", "hold"}
    )


def test_hiring_reason_catalog_twelve_codes_no_free_text_flag() -> None:
    catalog = list_hiring_reason_catalog()
    assert len(catalog) == 12
    assert len(HIRING_REASON_CODES) == 12
    codes = {item["code"] for item in catalog}
    assert codes == HIRING_REASON_CODES
    for item in catalog:
        assert "requires_description" not in item
        assert set(item.keys()) == {"code", "label", "allowed_decisions"}
        assert isinstance(item["label"], str) and item["label"]
        allowed = item["allowed_decisions"]
        assert isinstance(allowed, list) and allowed
        assert set(allowed) <= HIRING_DECISIONS


def test_hiring_decision_model_has_no_reason_text_column() -> None:
    columns = HiringDecision.__table__.c
    assert "reason" not in columns
    forbidden_substrings = ("quote", "summary", "offer")
    for name in columns.keys():
        lower = name.lower()
        for needle in forbidden_substrings:
            assert needle not in lower, f"forbidden column substring {needle!r} in {name}"
    required = {
        "id",
        "application_id",
        "decision",
        "reason_code",
        "round_id",
        "analysis_version_id",
        "overall_score",
        "analysis_version_no",
        "transcript_version_id",
        "job_version_id",
        "from_pipeline_status",
        "to_pipeline_status",
        "decided_by",
        "idempotency_key",
        "created_at",
    }
    assert required <= set(columns.keys())
    assert columns["reason_code"].nullable is False
    index_names = {idx.name for idx in HiringDecision.__table__.indexes}
    assert "uq_hiring_decisions_idempotency" in index_names
    assert "ix_hiring_decisions_application_id" in index_names
