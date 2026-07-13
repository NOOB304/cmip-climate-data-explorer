from __future__ import annotations

from .enums import FailureCode, TaskStatus
from .errors import InvalidTaskTransition

_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset({TaskStatus.RESOLVING, TaskStatus.CANCELED}),
    TaskStatus.RESOLVING: frozenset({TaskStatus.PROBING, TaskStatus.FAILED, TaskStatus.CANCELED}),
    TaskStatus.PROBING: frozenset({TaskStatus.DOWNLOADING, TaskStatus.FAILED, TaskStatus.CANCELED}),
    TaskStatus.DOWNLOADING: frozenset(
        {
            TaskStatus.PAUSED,
            TaskStatus.VERIFYING,
            TaskStatus.RETRY_WAIT,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.INTERRUPTED,
        }
    ),
    TaskStatus.PAUSED: frozenset({TaskStatus.DOWNLOADING, TaskStatus.CANCELED}),
    TaskStatus.VERIFYING: frozenset(
        {TaskStatus.PROCESSING, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
    ),
    TaskStatus.PROCESSING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED, TaskStatus.INTERRUPTED}
    ),
    TaskStatus.RETRY_WAIT: frozenset(
        {TaskStatus.RESOLVING, TaskStatus.DOWNLOADING, TaskStatus.FAILED, TaskStatus.CANCELED}
    ),
    TaskStatus.INTERRUPTED: frozenset(
        {TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PROCESSING, TaskStatus.CANCELED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELED}),
    TaskStatus.CANCELED: frozenset(),
}


def assert_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in _TRANSITIONS[current]:
        raise InvalidTaskTransition(
            code=FailureCode.VALIDATION_FAILED,
            message=f"invalid task transition: {current} -> {target}",
            details={"current": current, "target": target},
        )
