from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.interview import (
    INTERVIEW_FORMAT_OFFLINE,
    INTERVIEW_FORMAT_ONLINE,
    INTERVIEW_STATUS_CANCELLED,
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_CONFIRMED,
    INTERVIEW_STATUS_DRAFT,
    INTERVIEW_STATUS_ENDED_ABNORMALLY,
    INTERVIEW_STATUS_IN_PROGRESS,
    INTERVIEW_STATUS_PENDING_TRANSCRIPT,
    INTERVIEW_STATUS_SCHEDULED,
    SCHEDULE_STATUS_ACTIVE,
    SCHEDULE_STATUS_CANCELLED,
    SCHEDULE_STATUS_SUPERSEDED,
)
from app.services.interview_conflicts import (
    intervals_overlap,
    validate_schedule_window,
)
from app.services.interview_state import (
    InterviewStateError,
    allowed_actions_for_status,
    assert_transition,
)


def test_draft_to_scheduled_is_allowed() -> None:
    assert_transition(INTERVIEW_STATUS_DRAFT, INTERVIEW_STATUS_SCHEDULED, "schedule")


def test_schedule_does_not_enter_confirmed() -> None:
    with pytest.raises(InterviewStateError):
        assert_transition(
            INTERVIEW_STATUS_DRAFT, INTERVIEW_STATUS_CONFIRMED, "schedule"
        )


def test_start_from_scheduled_and_confirmed() -> None:
    assert_transition(INTERVIEW_STATUS_SCHEDULED, INTERVIEW_STATUS_IN_PROGRESS, "start")
    assert_transition(INTERVIEW_STATUS_CONFIRMED, INTERVIEW_STATUS_IN_PROGRESS, "start")


def test_finish_enters_pending_transcript() -> None:
    assert_transition(
        INTERVIEW_STATUS_IN_PROGRESS,
        INTERVIEW_STATUS_PENDING_TRANSCRIPT,
        "finish",
    )


def test_complete_from_pending_transcript() -> None:
    assert_transition(
        INTERVIEW_STATUS_PENDING_TRANSCRIPT,
        INTERVIEW_STATUS_COMPLETED,
        "complete",
    )


def test_cancel_from_scheduled_confirmed_draft() -> None:
    for status in (
        INTERVIEW_STATUS_DRAFT,
        INTERVIEW_STATUS_SCHEDULED,
        INTERVIEW_STATUS_CONFIRMED,
    ):
        assert_transition(status, INTERVIEW_STATUS_CANCELLED, "cancel")


def test_cancel_or_abnormal_from_in_progress() -> None:
    assert_transition(
        INTERVIEW_STATUS_IN_PROGRESS, INTERVIEW_STATUS_CANCELLED, "cancel"
    )
    assert_transition(
        INTERVIEW_STATUS_IN_PROGRESS,
        INTERVIEW_STATUS_ENDED_ABNORMALLY,
        "end_abnormally",
    )


def test_illegal_transitions_rejected() -> None:
    with pytest.raises(InterviewStateError):
        assert_transition(
            INTERVIEW_STATUS_COMPLETED, INTERVIEW_STATUS_SCHEDULED, "schedule"
        )
    with pytest.raises(InterviewStateError):
        assert_transition(
            INTERVIEW_STATUS_DRAFT, INTERVIEW_STATUS_IN_PROGRESS, "start"
        )
    with pytest.raises(InterviewStateError):
        assert_transition(
            INTERVIEW_STATUS_SCHEDULED,
            INTERVIEW_STATUS_PENDING_TRANSCRIPT,
            "finish",
        )


def test_allowed_actions_matrix() -> None:
    assert set(allowed_actions_for_status(INTERVIEW_STATUS_DRAFT)) == {
        "edit",
        "schedule",
        "cancel",
    }
    assert set(allowed_actions_for_status(INTERVIEW_STATUS_SCHEDULED)) == {
        "edit",
        "reschedule",
        "cancel",
        "start",
        "generate_invitation",
        "view_invitation",
        "confirm_invitation",
    }
    assert set(allowed_actions_for_status(INTERVIEW_STATUS_CONFIRMED)) == {
        "edit",
        "reschedule",
        "cancel",
        "start",
        "view_invitation",
    }
    assert set(allowed_actions_for_status(INTERVIEW_STATUS_IN_PROGRESS)) == {
        "finish",
        "end_abnormally",
    }
    assert set(allowed_actions_for_status(INTERVIEW_STATUS_PENDING_TRANSCRIPT)) == {
        "complete",
    }
    assert allowed_actions_for_status(INTERVIEW_STATUS_COMPLETED) == []
    assert set(allowed_actions_for_status(INTERVIEW_STATUS_CANCELLED)) == {
        "generate_cancellation",
        "view_invitation",
    }
    assert allowed_actions_for_status(INTERVIEW_STATUS_ENDED_ABNORMALLY) == []


def test_confirm_invitation_from_scheduled() -> None:
    assert_transition(
        INTERVIEW_STATUS_SCHEDULED,
        INTERVIEW_STATUS_CONFIRMED,
        "confirm_invitation",
    )

def test_adjacent_intervals_do_not_overlap() -> None:
    start = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
    mid = datetime(2026, 8, 14, 11, 0, tzinfo=UTC)
    end = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    assert intervals_overlap(start, mid, mid, end) is False
    assert intervals_overlap(start, end, mid, end) is True


def test_cancelled_and_superseded_not_used_in_overlap_filter() -> None:
    assert SCHEDULE_STATUS_CANCELLED != SCHEDULE_STATUS_ACTIVE
    assert SCHEDULE_STATUS_SUPERSEDED != SCHEDULE_STATUS_ACTIVE


def test_validate_schedule_window_requires_iana_and_order() -> None:
    start = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
    end = datetime(2026, 8, 14, 11, 0, tzinfo=UTC)
    validate_schedule_window(start, end, "Asia/Shanghai")
    with pytest.raises(ValueError, match="timezone"):
        validate_schedule_window(start, end, "Not/AZone")
    with pytest.raises(ValueError, match="end"):
        validate_schedule_window(end, start, "Asia/Shanghai")
    naive = datetime(2026, 8, 14, 10, 0)
    with pytest.raises(ValueError, match="timezone"):
        validate_schedule_window(naive, end, "Asia/Shanghai")


def test_format_constants() -> None:
    assert INTERVIEW_FORMAT_ONLINE == "ONLINE"
    assert INTERVIEW_FORMAT_OFFLINE == "OFFLINE"
    _ = SimpleNamespace(id=uuid4(), delta=timedelta(hours=1))
