from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import FailureCode


@dataclass(slots=True)
class ExplorerError(Exception):
    code: FailureCode
    message: str
    retriable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class InvalidTaskTransition(ExplorerError):
    pass


class FullDownloadConfirmationRequired(ExplorerError):
    pass
