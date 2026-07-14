from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from cmip_explorer import __version__
from cmip_explorer.infrastructure.download import (
    DEFAULT_RECONNECT_DELAYS,
    HttpRangeDownloader,
)

UPDATE_REPOSITORY = "NOOB304/cmip-climate-data-explorer"
GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"
_INSTALLER_PATTERN = re.compile(
    r"^CMIP-Climate-Explorer-.+-x64-Setup\.exe$", re.IGNORECASE
)
_VERSION_PATTERN = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]([0-9A-Za-z.-]+))?$"
)


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    url: str
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    tag_name: str
    name: str
    notes: str
    page_url: str
    installer: ReleaseAsset
    checksum: ReleaseAsset
    prerelease: bool = False


class GitHubReleaseUpdater:
    def __init__(
        self,
        repository: str = UPDATE_REPOSITORY,
        *,
        current_version: str = __version__,
        channel: str = "stable",
        client: httpx.AsyncClient | None = None,
        reconnect_delays: tuple[float, ...] = DEFAULT_RECONNECT_DELAYS,
    ) -> None:
        repository = repository.strip().strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise UpdateError("软件更新仓库尚未配置")
        if channel not in {"stable", "preview"}:
            raise UpdateError(f"不支持的更新通道: {channel}")
        self.repository = repository
        self.current_version = current_version
        self.channel = channel
        self.reconnect_delays = tuple(max(0.0, delay) for delay in reconnect_delays)
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(45, connect=15, read=30),
            follow_redirects=True,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"CMIP-Climate-Explorer/{current_version}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def check(self) -> ReleaseInfo | None:
        if self.channel == "stable":
            latest = await self._stable_release_without_api()
        else:
            response = await self._get_with_retries(
                f"{GITHUB_API}/repos/{self.repository}/releases",
                params={"per_page": 20},
            )
            candidates = tuple(response.json())
            releases = [
                parsed
                for payload in candidates
                if not payload.get("draft")
                if (parsed := _parse_release(payload)) is not None
            ]
            if not releases:
                raise UpdateError("GitHub Release 中没有可验证的 Windows 安装包")
            latest = max(releases, key=lambda item: _version_key(item.version))
        if _version_key(latest.version) <= _version_key(self.current_version):
            return None
        return latest

    async def _stable_release_without_api(self) -> ReleaseInfo:
        response = await self._get_with_retries(
            f"{GITHUB_WEB}/{self.repository}/releases/latest",
            headers={"Accept": "text/html"},
        )
        parts = urlsplit(str(response.url)).path.rstrip("/").split("/")
        if len(parts) < 2 or parts[-2] != "tag":
            raise UpdateError("GitHub 没有返回最新正式版标签")
        tag_name = unquote(parts[-1])
        match = _VERSION_PATTERN.fullmatch(tag_name)
        if match is None or match.group(4) is not None:
            raise UpdateError(f"GitHub 最新正式版标签无效: {tag_name}")
        version = tag_name.removeprefix("v")
        installer_name = f"CMIP-Climate-Explorer-{version}-x64-Setup.exe"
        download_root = f"{GITHUB_WEB}/{self.repository}/releases/download/{tag_name}"
        return ReleaseInfo(
            version=version,
            tag_name=tag_name,
            name=f"CMIP Climate Explorer {version}",
            notes="",
            page_url=str(response.url),
            installer=ReleaseAsset(
                name=installer_name,
                url=f"{download_root}/{installer_name}",
            ),
            checksum=ReleaseAsset(
                name="SHA256SUMS.txt",
                url=f"{download_root}/SHA256SUMS.txt",
            ),
        )

    async def download(
        self,
        release: ReleaseInfo,
        target_directory: Path,
        progress=None,
        reconnect: Callable[[int, int, float, Exception], None] | None = None,
    ) -> Path:
        target_directory.mkdir(parents=True, exist_ok=True)
        if Path(release.installer.name).name != release.installer.name:
            raise UpdateError("Release 安装包文件名无效")
        checksum = await self._expected_checksum(release, reconnect=reconnect)
        target = target_directory / release.installer.name
        downloader = HttpRangeDownloader(
            client=self.client,
            reconnect_delays=self.reconnect_delays,
            request_chunk_bytes=8 * 1024 * 1024,
        )
        await downloader.download(
            release.installer.url,
            target,
            expected_size=release.installer.size_bytes,
            expected_checksum=checksum,
            checksum_type="SHA256",
            progress=progress,
            reconnect=reconnect,
        )
        return target

    async def _expected_checksum(
        self,
        release: ReleaseInfo,
        *,
        reconnect: Callable[[int, int, float, Exception], None] | None = None,
    ) -> str:
        response = await self._get_with_retries(
            release.checksum.url,
            reconnect=reconnect,
        )
        for line in response.text.splitlines():
            match = re.search(r"\b([0-9a-fA-F]{64})\b", line)
            if match and (
                release.installer.name in line or len(response.text.splitlines()) == 1
            ):
                return match.group(1).lower()
        raise UpdateError("Release 校验文件中没有找到安装包 SHA-256")

    async def _get_with_retries(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        reconnect: Callable[[int, int, float, Exception], None] | None = None,
    ) -> httpx.Response:
        for retry_index in range(len(self.reconnect_delays) + 1):
            try:
                response = await self.client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                if retry_index >= len(self.reconnect_delays) or not _is_retryable(exc):
                    raise
                delay = self.reconnect_delays[retry_index]
                if reconnect:
                    reconnect(retry_index + 1, len(self.reconnect_delays), delay, exc)
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable update reconnect state")


def _parse_release(payload: dict[str, Any]) -> ReleaseInfo | None:
    tag_name = str(payload.get("tag_name") or "")
    version_match = _VERSION_PATTERN.fullmatch(tag_name)
    if version_match is None:
        return None
    assets = tuple(payload.get("assets") or ())
    installer_payload = next(
        (
            asset
            for asset in assets
            if _INSTALLER_PATTERN.fullmatch(str(asset.get("name") or ""))
        ),
        None,
    )
    if installer_payload is None:
        return None
    installer_name = str(installer_payload["name"])
    checksum_payload = next(
        (
            asset
            for asset in assets
            if str(asset.get("name") or "")
            in {f"{installer_name}.sha256", "SHA256SUMS.txt"}
        ),
        None,
    )
    if checksum_payload is None:
        return None
    return ReleaseInfo(
        version=tag_name.removeprefix("v"),
        tag_name=tag_name,
        name=str(payload.get("name") or tag_name),
        notes=str(payload.get("body") or ""),
        page_url=str(payload.get("html_url") or ""),
        installer=_release_asset(installer_payload),
        checksum=_release_asset(checksum_payload),
        prerelease=bool(payload.get("prerelease")),
    )


def _release_asset(payload: dict[str, Any]) -> ReleaseAsset:
    size = payload.get("size")
    return ReleaseAsset(
        name=str(payload.get("name") or ""),
        url=str(payload.get("browser_download_url") or ""),
        size_bytes=int(size) if size is not None else None,
    )


def _version_key(value: str) -> tuple[int, int, int, int, str]:
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise UpdateError(f"无法识别版本号: {value}")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    prerelease = match.group(4)
    return major, minor, patch, 1 if prerelease is None else 0, prerelease or ""


def _is_retryable(error: httpx.HTTPError) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status in {408, 425, 429} or 500 <= status < 600
    return isinstance(error, httpx.TransportError)
