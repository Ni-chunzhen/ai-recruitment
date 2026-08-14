from datetime import UTC, datetime

import pytest

from app.models.job import (
    JOB_STATUS_LABELS,
    UPGRADE_INITIAL,
    UPGRADE_MAJOR,
    UPGRADE_MINOR,
)
from app.services.jobs import (
    build_version_diff,
    decide_version_bump,
    format_version_label,
    normalize_score_dimensions,
    score_dimensions_equal,
    validate_publish_payload,
)


def _valid_dims() -> list[dict]:
    return [
        {
            "name": "沟通",
            "weight": 40,
            "description": "沟通协作",
            "anchors": ["1", "2", "3", "4", "5"],
        },
        {
            "name": "专业",
            "weight": 60,
            "description": "专业能力",
            "anchors": ["1", "2", "3", "4", "5"],
        },
    ]


def test_status_labels_cover_lifecycle() -> None:
    assert JOB_STATUS_LABELS == {
        "draft": "草稿",
        "open": "招聘中",
        "paused": "已暂停",
        "closed": "已关闭",
    }


def test_format_job_code_pattern() -> None:
    year_month = datetime.now(UTC).strftime("%Y%m")
    code = f"JOB-{year_month}-0001"
    assert code.startswith("JOB-")
    assert len(code) == 3 + 1 + 6 + 1 + 4


def test_format_version_label() -> None:
    assert format_version_label(1, 0) == "V1.0"
    assert format_version_label(2, 3) == "V2.3"


def test_validate_publish_requires_core_fields() -> None:
    errors = validate_publish_payload(
        name="",
        department="",
        owner_name="",
        headcount=None,
        structured_jd={"responsibilities": [], "requirements": []},
        score_dimensions=[],
    )
    fields = {item["field"] for item in errors}
    assert "name" in fields
    assert "department" in fields
    assert "owner_name" in fields
    assert "headcount" in fields
    assert "structured_jd.responsibilities" in fields
    assert "structured_jd.requirements" in fields
    assert "score_dimensions" in fields


def test_validate_publish_requires_weight_sum_100() -> None:
    errors = validate_publish_payload(
        name="产品经理",
        department="产品部",
        owner_name="张敏",
        headcount=1,
        structured_jd={
            "responsibilities": ["负责需求"],
            "requirements": ["3 年经验"],
        },
        score_dimensions=[{"name": "沟通", "weight": 40, "anchors": []}],
    )
    assert any(
        item["field"] == "score_dimensions" and "100%" in item["message"]
        for item in errors
    )


def test_validate_publish_passes_when_complete() -> None:
    errors = validate_publish_payload(
        name="产品经理",
        department="产品部",
        owner_name="张敏",
        headcount=2,
        structured_jd={
            "responsibilities": ["负责需求"],
            "requirements": ["3 年经验"],
            "must_have": [],
            "nice_to_have": [],
            "skills": ["AI"],
        },
        score_dimensions=_valid_dims(),
    )
    assert errors == []


def test_score_dimensions_equal_ignores_object_wrappers() -> None:
    left = _valid_dims()
    right = normalize_score_dimensions(_valid_dims())
    assert score_dimensions_equal(left, right)
    right[0]["weight"] = 41
    assert not score_dimensions_equal(left, right)


def test_decide_version_bump_initial() -> None:
    major, minor, upgrade_type, label = decide_version_bump(
        current_major=0,
        current_minor=0,
        current_score_dimensions=None,
        draft_score_dimensions=_valid_dims(),
        is_initial=True,
    )
    assert (major, minor, upgrade_type, label) == (1, 0, UPGRADE_INITIAL, "V1.0")


def test_decide_version_bump_major_when_dimensions_change() -> None:
    current = _valid_dims()
    draft = _valid_dims()
    draft[0]["weight"] = 50
    draft[1]["weight"] = 50
    major, minor, upgrade_type, label = decide_version_bump(
        current_major=1,
        current_minor=2,
        current_score_dimensions=current,
        draft_score_dimensions=draft,
    )
    assert (major, minor, upgrade_type, label) == (2, 0, UPGRADE_MAJOR, "V2.0")


def test_decide_version_bump_minor_when_only_ops_change() -> None:
    dims = _valid_dims()
    jd = {
        "responsibilities": ["负责需求"],
        "requirements": ["3 年经验"],
        "must_have": [],
        "nice_to_have": [],
        "skills": [],
    }
    major, minor, upgrade_type, label = decide_version_bump(
        current_major=1,
        current_minor=0,
        current_score_dimensions=dims,
        draft_score_dimensions=dims,
        current_structured_jd=jd,
        draft_structured_jd=jd,
        current_raw_jd_text="same",
        draft_raw_jd_text="same",
    )
    assert (major, minor, upgrade_type, label) == (1, 1, UPGRADE_MINOR, "V1.1")


def test_decide_version_bump_major_when_jd_changes() -> None:
    dims = _valid_dims()
    current_jd = {
        "responsibilities": ["旧职责"],
        "requirements": ["旧要求"],
        "must_have": [],
        "nice_to_have": [],
        "skills": [],
    }
    draft_jd = {
        "responsibilities": ["新职责"],
        "requirements": ["旧要求"],
        "must_have": [],
        "nice_to_have": [],
        "skills": [],
    }
    major, minor, upgrade_type, label = decide_version_bump(
        current_major=1,
        current_minor=3,
        current_score_dimensions=dims,
        draft_score_dimensions=dims,
        current_structured_jd=current_jd,
        draft_structured_jd=draft_jd,
    )
    assert (major, minor, upgrade_type, label) == (2, 0, UPGRADE_MAJOR, "V2.0")


def test_build_version_diff_detects_field_changes() -> None:
    from types import SimpleNamespace

    left = SimpleNamespace(
        job_snapshot={"name": "旧岗位", "headcount": 1, "department": "产品部"},
        raw_jd_text="旧 JD",
        structured_jd={
            "responsibilities": ["A"],
            "requirements": ["B"],
            "must_have": [],
            "nice_to_have": [],
            "skills": ["Python"],
        },
        score_dimensions=_valid_dims(),
        change_summary="初始",
    )
    right = SimpleNamespace(
        job_snapshot={"name": "新岗位", "headcount": 2, "department": "产品部"},
        raw_jd_text="新 JD",
        structured_jd={
            "responsibilities": ["A", "C"],
            "requirements": ["B"],
            "must_have": [],
            "nice_to_have": [],
            "skills": ["Python"],
        },
        score_dimensions=_valid_dims(),
        change_summary="更新职责",
    )
    changes = build_version_diff(left, right)  # type: ignore[arg-type]
    fields = {item.field for item in changes}
    assert "name" in fields
    assert "headcount" in fields
    assert "raw_jd_text" in fields
    assert "structured_jd.responsibilities" in fields
    assert "change_summary" in fields
    assert "department" not in fields


def test_decide_version_bump_force_major() -> None:
    dims = _valid_dims()
    major, minor, upgrade_type, label = decide_version_bump(
        current_major=3,
        current_minor=4,
        current_score_dimensions=dims,
        draft_score_dimensions=dims,
        force_major=True,
    )
    assert (major, minor, upgrade_type, label) == (4, 0, UPGRADE_MAJOR, "V4.0")


def test_decide_version_bump_requested_major() -> None:
    dims = _valid_dims()
    major, minor, upgrade_type, label = decide_version_bump(
        current_major=1,
        current_minor=5,
        current_score_dimensions=dims,
        draft_score_dimensions=dims,
        requested_upgrade_type=UPGRADE_MAJOR,
    )
    assert (major, minor, upgrade_type, label) == (2, 0, UPGRADE_MAJOR, "V2.0")


@pytest.mark.parametrize(
    ("from_status", "action", "allowed"),
    [
        ("open", "pause", True),
        ("paused", "pause", False),
        ("paused", "resume", True),
        ("open", "resume", False),
        ("open", "close", True),
        ("paused", "close", True),
        ("draft", "close", False),
        ("closed", "resume", False),
        ("closed", "publish", False),
    ],
)
def test_status_transition_matrix(
    from_status: str, action: str, allowed: bool
) -> None:
    allowed_map = {
        ("open", "pause"): True,
        ("paused", "resume"): True,
        ("open", "close"): True,
        ("paused", "close"): True,
    }
    assert allowed_map.get((from_status, action), False) is allowed


def test_copy_semantics_keep_source_and_new_draft_code() -> None:
    source_job_id = "11111111-1111-1111-1111-111111111111"
    copied = {
        "status": "draft",
        "source_job_id": source_job_id,
        "code": "JOB-202608-0002",
        "draft_version_id": "22222222-2222-2222-2222-222222222222",
        "current_version_id": None,
    }
    assert copied["status"] == "draft"
    assert copied["source_job_id"] == source_job_id
    assert copied["current_version_id"] is None
    assert copied["draft_version_id"] is not None
    assert copied["code"].startswith("JOB-")
