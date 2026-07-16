from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from cmip_explorer.infrastructure.update import (
    GitHubReleaseUpdater,
    ReleaseAsset,
    ReleaseInfo,
    UpdateError,
)


def _release(version: str, installer_size: int, *, checksum: bool = True) -> dict:
    installer_name = f"CMIP-Climate-Explorer-{version}-x64-Setup.exe"
    assets = [
        {
            "name": installer_name,
            "browser_download_url": "https://downloads.test/setup.exe",
            "size": installer_size,
        }
    ]
    if checksum:
        assets.append(
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://downloads.test/SHA256SUMS.txt",
                "size": 100,
            }
        )
    return {
        "tag_name": f"v{version}",
        "name": f"Version {version}",
        "body": "release notes",
        "html_url": f"https://github.test/releases/v{version}",
        "draft": False,
        "prerelease": False,
        "assets": assets,
    }


async def test_update_service_checks_downloads_and_verifies_release(tmp_path: Path) -> None:
    installer = b"verified windows installer"
    digest = hashlib.sha256(installer).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                302,
                headers={
                    "Location": "https://github.com/owner/repository/releases/tag/v0.3.0"
                },
            )
        if request.url.path.endswith("/releases/tag/v0.3.0"):
            return httpx.Response(200, text="release")
        if request.url.path.endswith("SHA256SUMS.txt"):
            return httpx.Response(
                200,
                text=(
                    f"{digest}  CMIP-Climate-Explorer-0.3.0-x64-Setup.exe\n"
                ),
            )
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"Content-Length": str(len(installer)), "ETag": '"update"'},
            )
        return httpx.Response(200, content=installer)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        progress_events: list[tuple[int, int | None]] = []
        updater = GitHubReleaseUpdater(
            "owner/repository", current_version="0.2.9", client=client
        )
        available = await updater.check()
        assert available is not None
        result = await updater.download(
            available,
            tmp_path,
            progress=lambda written, total: progress_events.append((written, total)),
        )

    assert result.read_bytes() == installer
    assert progress_events[-1] == (len(installer), len(installer))


async def test_update_service_returns_none_for_current_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                302,
                headers={
                    "Location": "https://github.com/owner/repository/releases/tag/v0.2.9"
                },
            )
        return httpx.Response(200, text="release")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        updater = GitHubReleaseUpdater(
            "owner/repository", current_version="0.2.9", client=client
        )
        assert await updater.check() is None


async def test_update_service_retries_checksum_request(tmp_path: Path) -> None:
    installer = b"verified installer after reconnect"
    digest = hashlib.sha256(installer).hexdigest()
    installer_name = "CMIP-Climate-Explorer-0.3.0-x64-Setup.exe"
    checksum_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal checksum_calls
        if request.url.path.endswith("SHA256SUMS.txt"):
            checksum_calls += 1
            if checksum_calls == 1:
                return httpx.Response(503)
            return httpx.Response(200, text=f"{digest}  {installer_name}\n")
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"Content-Length": str(len(installer))},
            )
        return httpx.Response(200, content=installer)

    release = ReleaseInfo(
        version="0.3.0",
        tag_name="v0.3.0",
        name="Version 0.3.0",
        notes="",
        page_url="https://github.test/releases/v0.3.0",
        installer=ReleaseAsset(
            installer_name,
            "https://downloads.test/setup.exe",
            len(installer),
        ),
        checksum=ReleaseAsset(
            "SHA256SUMS.txt",
            "https://downloads.test/SHA256SUMS.txt",
        ),
    )
    reconnects: list[tuple[int, int, float]] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        updater = GitHubReleaseUpdater(
            "owner/repository",
            current_version="0.2.9",
            client=client,
            reconnect_delays=(0,),
        )
        result = await updater.download(
            release,
            tmp_path,
            reconnect=lambda attempt, maximum, delay, _error: reconnects.append(
                (attempt, maximum, delay)
            ),
        )

    assert result.read_bytes() == installer
    assert reconnects == [(1, 1, 0)]


async def test_update_service_rejects_release_without_checksum() -> None:
    release = _release("0.3.0", 10, checksum=False)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=[release])
        )
    ) as client:
        updater = GitHubReleaseUpdater(
            "owner/repository",
            current_version="0.2.9",
            channel="preview",
            client=client,
        )
        with pytest.raises(UpdateError, match="没有可验证"):
            await updater.check()


def test_installer_supports_silent_update_and_old_updater_bridge() -> None:
    installer_script = (
        Path(__file__).parents[2] / "packaging" / "installer.iss"
    ).read_text(encoding="utf-8")

    assert "HasCommandLineSwitch('/CLOSEAPPLICATIONS')" in installer_script
    assert "function IsStagedUpdate(): Boolean;" in installer_script
    assert "function IsDeferredRelaunch(): Boolean;" in installer_script
    assert "function ShouldRelaunchUpdate(): Boolean;" in installer_script
    assert "IsUpdateMode() and (not IsStagedUpdate())" in installer_script
    assert "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS " in installer_script
    assert "/FORCECLOSEAPPLICATIONS /UPDATE=1 /STAGEDUPDATE=1" in installer_script
    assert "ExpandConstant('{param:TARGETDIR|}')" in installer_script
    assert "TargetArguments" in installer_script
    assert "GetDefaultInstallDir" in installer_script
    assert "ExpandConstant('{srcexe}')" in installer_script
    assert "CMIPClimateExplorerUpdate.cmd" in installer_script
    assert 'Flags: nowait; Check: ShouldRelaunchUpdate' in installer_script
    assert "skipifsilent; Check: IsNotUpdateMode" in installer_script
