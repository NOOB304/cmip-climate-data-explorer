from uuid import uuid4

import pytest
from pydantic import ValidationError

from cmip_explorer.domain.enums import DownloadMode, TaskStatus
from cmip_explorer.domain.errors import InvalidTaskTransition
from cmip_explorer.domain.models import DownloadTask
from cmip_explorer.domain.state_machine import assert_transition


def test_valid_transition() -> None:
    assert_transition(TaskStatus.QUEUED, TaskStatus.RESOLVING)


def test_terminal_transition_is_rejected() -> None:
    with pytest.raises(InvalidTaskTransition):
        assert_transition(TaskStatus.COMPLETED, TaskStatus.DOWNLOADING)


def test_full_download_requires_confirmation() -> None:
    with pytest.raises(ValidationError, match="confirmation_id"):
        DownloadTask(
            job_id=uuid4(),
            file_key="CMIP6.example.nc",
            mode=DownloadMode.FULL_FILE,
            source_url="https://example.test/file.nc",
            target_path="file.nc",
        )
