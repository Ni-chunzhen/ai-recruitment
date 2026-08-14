from app.models.interview import (
    INTERVIEW_STATUS_CANCELLED,
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_CONFIRMED,
    INTERVIEW_STATUS_DRAFT,
    INTERVIEW_STATUS_ENDED_ABNORMALLY,
    INTERVIEW_STATUS_IN_PROGRESS,
    INTERVIEW_STATUS_PENDING_TRANSCRIPT,
    INTERVIEW_STATUS_SCHEDULED,
)


class InterviewStateError(Exception):
    pass


_TRANSITIONS: dict[tuple[str, str], str] = {
    ("schedule", INTERVIEW_STATUS_DRAFT): INTERVIEW_STATUS_SCHEDULED,
    ("start", INTERVIEW_STATUS_SCHEDULED): INTERVIEW_STATUS_IN_PROGRESS,
    ("start", INTERVIEW_STATUS_CONFIRMED): INTERVIEW_STATUS_IN_PROGRESS,
    ("finish", INTERVIEW_STATUS_IN_PROGRESS): INTERVIEW_STATUS_PENDING_TRANSCRIPT,
    ("complete", INTERVIEW_STATUS_PENDING_TRANSCRIPT): INTERVIEW_STATUS_COMPLETED,
    ("cancel", INTERVIEW_STATUS_DRAFT): INTERVIEW_STATUS_CANCELLED,
    ("cancel", INTERVIEW_STATUS_SCHEDULED): INTERVIEW_STATUS_CANCELLED,
    ("cancel", INTERVIEW_STATUS_CONFIRMED): INTERVIEW_STATUS_CANCELLED,
    ("cancel", INTERVIEW_STATUS_IN_PROGRESS): INTERVIEW_STATUS_CANCELLED,
    ("end_abnormally", INTERVIEW_STATUS_IN_PROGRESS): INTERVIEW_STATUS_ENDED_ABNORMALLY,
}

_ACTIONS_BY_STATUS: dict[str, tuple[str, ...]] = {
    INTERVIEW_STATUS_DRAFT: ("edit", "schedule", "cancel"),
    INTERVIEW_STATUS_SCHEDULED: ("edit", "reschedule", "cancel", "start"),
    INTERVIEW_STATUS_CONFIRMED: ("edit", "reschedule", "cancel", "start"),
    INTERVIEW_STATUS_IN_PROGRESS: ("finish", "end_abnormally"),
    INTERVIEW_STATUS_PENDING_TRANSCRIPT: ("complete",),
    INTERVIEW_STATUS_COMPLETED: (),
    INTERVIEW_STATUS_CANCELLED: (),
    INTERVIEW_STATUS_ENDED_ABNORMALLY: (),
}

MANAGE_ONLY_ACTIONS = frozenset(
    {"edit", "schedule", "reschedule", "cancel", "complete"}
)
EXECUTE_ACTIONS = frozenset({"start", "finish", "end_abnormally"})


def assert_transition(current: str, target: str, action: str) -> None:
    expected = _TRANSITIONS.get((action, current))
    if expected is None or expected != target:
        raise InterviewStateError(
            f"illegal interview status transition: {current} --{action}--> {target}"
        )


def next_status(current: str, action: str) -> str:
    expected = _TRANSITIONS.get((action, current))
    if expected is None:
        raise InterviewStateError(
            f"illegal interview status transition: {current} --{action}"
        )
    return expected


def allowed_actions_for_status(status: str) -> list[str]:
    return list(_ACTIONS_BY_STATUS.get(status, ()))


def filter_actions_for_actor(
    status: str,
    *,
    can_manage: bool,
    can_execute: bool,
) -> list[str]:
    actions = allowed_actions_for_status(status)
    if can_manage:
        return actions
    if can_execute:
        return [action for action in actions if action in EXECUTE_ACTIONS]
    return []
