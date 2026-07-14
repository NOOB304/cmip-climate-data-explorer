from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

DEFAULT_RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 60.0)


class DownloadPaused(Exception):
    pass


class DownloadCancelled(Exception):
    pass


class DownloadIncompleteError(OSError):
    pass


@dataclass(slots=True)
class DownloadControl:
    pause_requested: bool = False
    cancel_requested: bool = False


@dataclass(frozen=True, slots=True)
class PartMetadata:
    source_url: str
    etag: str | None
    last_modified: str | None
    expected_size: int | None


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    bytes_written: int
    sha256: str
    resumed_from: int


class HttpRangeDownloader:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        chunk_size: int = 1024 * 1024,
        reconnect_delays: tuple[float, ...] = DEFAULT_RECONNECT_DELAYS,
        request_chunk_bytes: int | None = None,
    ) -> None:
        self.client = client
        self.chunk_size = chunk_size
        self.reconnect_delays = tuple(max(0.0, delay) for delay in reconnect_delays)
        self.request_chunk_bytes = (
            max(chunk_size, request_chunk_bytes) if request_chunk_bytes else None
        )

    @staticmethod
    def _new_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0, read=15.0),
            follow_redirects=True,
            headers={"User-Agent": "CMIP-Climate-Explorer/0.1"},
        )

    async def download(
        self,
        url: str,
        target: Path,
        *,
        expected_size: int | None = None,
        expected_checksum: str | None = None,
        checksum_type: str = "SHA256",
        progress: Callable[[int, int | None], None] | None = None,
        control: DownloadControl | None = None,
        reconnect: Callable[[int, int, float, Exception], None] | None = None,
    ) -> DownloadResult:
        if self.client is not None:
            return await self._download_with_retries(
                self.client,
                url,
                target,
                expected_size=expected_size,
                expected_checksum=expected_checksum,
                checksum_type=checksum_type,
                progress=progress,
                control=control,
                reconnect=reconnect,
            )
        async with self._new_client() as client:
            return await self._download_with_retries(
                client,
                url,
                target,
                expected_size=expected_size,
                expected_checksum=expected_checksum,
                checksum_type=checksum_type,
                progress=progress,
                control=control,
                reconnect=reconnect,
            )

    async def _download_with_retries(
        self,
        client: httpx.AsyncClient,
        url: str,
        target: Path,
        *,
        expected_size: int | None,
        expected_checksum: str | None,
        checksum_type: str,
        progress: Callable[[int, int | None], None] | None,
        control: DownloadControl | None,
        reconnect: Callable[[int, int, float, Exception], None] | None,
    ) -> DownloadResult:
        for retry_index in range(len(self.reconnect_delays) + 1):
            try:
                return await self._download_with_client(
                    client,
                    url,
                    target,
                    expected_size=expected_size,
                    expected_checksum=expected_checksum,
                    checksum_type=checksum_type,
                    progress=progress,
                    control=control,
                )
            except (DownloadPaused, DownloadCancelled):
                raise
            except Exception as exc:
                if retry_index >= len(self.reconnect_delays) or not _is_retryable(exc):
                    raise
                delay = self.reconnect_delays[retry_index]
                if reconnect:
                    reconnect(retry_index + 1, len(self.reconnect_delays), delay, exc)
                await _wait_before_reconnect(delay, control)
        raise RuntimeError("unreachable download reconnect state")

    async def _download_with_client(
        self,
        client: httpx.AsyncClient,
        url: str,
        target: Path,
        *,
        expected_size: int | None = None,
        expected_checksum: str | None = None,
        checksum_type: str = "SHA256",
        progress: Callable[[int, int | None], None] | None = None,
        control: DownloadControl | None = None,
    ) -> DownloadResult:
        normalized_checksum = checksum_type.upper().replace("-", "")
        if normalized_checksum not in {"SHA256", "MD5"} and expected_checksum:
            raise ValueError(f"unsupported checksum type: {checksum_type}")
        control = control or DownloadControl()
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(target.suffix + ".part")
        sidecar = target.with_suffix(target.suffix + ".part.json")
        remote = await self._remote_metadata(client, url, expected_size)
        resume_at = self._resume_offset(
            part,
            sidecar,
            remote,
            allow_weak=bool(expected_checksum),
        )
        initial_resume_at = resume_at
        written = resume_at

        while remote.expected_size is None or written < remote.expected_size:
            requested_end = _range_end(
                written, remote.expected_size, self.request_chunk_bytes
            )
            range_requested = written > 0 or requested_end is not None
            range_value = (
                f"bytes={written}-{requested_end}"
                if requested_end is not None
                else f"bytes={written}-"
            )
            headers = {"Range": range_value} if range_requested else {}
            segment_start = written

            async with client.stream("GET", url, headers=headers) as response:
                if written and response.status_code != 206:
                    part.unlink(missing_ok=True)
                    sidecar.unlink(missing_ok=True)
                    return await self._download_with_client(
                        client,
                        url,
                        target,
                        expected_size=expected_size,
                        expected_checksum=expected_checksum,
                        checksum_type=checksum_type,
                        progress=progress,
                        control=control,
                    )
                response.raise_for_status()
                if response.status_code == 206:
                    _validate_content_range(response, written, requested_end)
                sidecar.write_text(
                    json.dumps(asdict(remote), sort_keys=True), encoding="utf-8"
                )
                mode = "ab" if written else "wb"
                with part.open(mode) as destination:
                    async for chunk in response.aiter_bytes(self.chunk_size):
                        if control.cancel_requested:
                            raise DownloadCancelled()
                        while control.pause_requested:
                            if control.cancel_requested:
                                raise DownloadCancelled()
                            await asyncio.sleep(0.2)
                        destination.write(chunk)
                        written += len(chunk)
                        if progress:
                            progress(written, remote.expected_size)
                        await asyncio.sleep(0)
                    destination.flush()
                    os.fsync(destination.fileno())

            if remote.expected_size is None or written >= remote.expected_size:
                break
            if response.status_code != 206 or written <= segment_start:
                raise DownloadIncompleteError(
                    f"download stopped at {written} of {remote.expected_size} bytes"
                )
            if requested_end is not None and written != requested_end + 1:
                raise DownloadIncompleteError(
                    f"range ended at {written - 1}, expected {requested_end}"
                )

        if remote.expected_size is not None and part.stat().st_size != remote.expected_size:
            actual_size = part.stat().st_size
            raise DownloadIncompleteError(
                f"download size mismatch: expected {remote.expected_size}, got {actual_size}"
            )
        validation_digest = _digest(part, normalized_checksum)
        if expected_checksum and validation_digest.casefold() != expected_checksum.casefold():
            raise OSError(f"download {normalized_checksum} checksum mismatch")
        sha256 = validation_digest if normalized_checksum == "SHA256" else _digest(part, "SHA256")
        os.replace(part, target)
        sidecar.unlink(missing_ok=True)
        return DownloadResult(target, target.stat().st_size, sha256, initial_resume_at)

    async def _remote_metadata(
        self, client: httpx.AsyncClient, url: str, expected_size: int | None
    ) -> PartMetadata:
        try:
            response = await client.head(url)
            response.raise_for_status()
            size_header = response.headers.get("Content-Length")
            size = int(size_header) if size_header else expected_size
            return PartMetadata(
                source_url=url,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                expected_size=size,
            )
        except httpx.HTTPError:
            return PartMetadata(url, None, None, expected_size)

    @staticmethod
    def _resume_offset(
        part: Path,
        sidecar: Path,
        remote: PartMetadata,
        *,
        allow_weak: bool = False,
    ) -> int:
        if not part.exists() or not sidecar.exists():
            return 0
        try:
            saved = PartMetadata(**json.loads(sidecar.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return 0
        same_resource = _same_resource(saved.source_url, remote.source_url)
        same_size = saved.expected_size == remote.expected_size
        exact_validators = (
            saved.etag == remote.etag and saved.last_modified == remote.last_modified
        )
        validators_match = same_resource and same_size and (
            exact_validators or (allow_weak and remote.expected_size is not None)
        )
        if not validators_match:
            return 0
        size = part.stat().st_size
        if remote.expected_size is not None and size >= remote.expected_size:
            return 0
        return size

    async def close(self) -> None:
        return None


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm.lower())
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_resource(left: str, right: str) -> bool:
    left_parts = urlsplit(left)
    right_parts = urlsplit(right)
    return (
        left_parts.hostname == right_parts.hostname
        and left_parts.path == right_parts.path
        and left_parts.query == right_parts.query
    )


def _range_end(start: int, total: int | None, chunk_bytes: int | None) -> int | None:
    if total is None or chunk_bytes is None:
        return None
    return min(total - 1, start + chunk_bytes - 1)


def _validate_content_range(
    response: httpx.Response, requested_start: int, requested_end: int | None
) -> None:
    value = response.headers.get("Content-Range", "")
    if not value:
        return
    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(?:\d+|\*)", value)
    if match is None:
        raise DownloadIncompleteError("range response did not include a valid Content-Range")
    actual_start, actual_end = (int(item) for item in match.groups())
    if actual_start != requested_start:
        raise DownloadIncompleteError(
            f"range started at {actual_start}, expected {requested_start}"
        )
    if requested_end is not None and actual_end != requested_end:
        raise DownloadIncompleteError(
            f"range ended at {actual_end}, expected {requested_end}"
        )


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, DownloadIncompleteError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status in {408, 425, 429} or 500 <= status < 600
    return isinstance(error, httpx.TransportError)


async def _wait_before_reconnect(
    delay: float, control: DownloadControl | None
) -> None:
    remaining = delay
    while remaining > 0:
        if control and control.cancel_requested:
            raise DownloadCancelled()
        while control and control.pause_requested:
            if control.cancel_requested:
                raise DownloadCancelled()
            await asyncio.sleep(0.2)
        interval = min(0.2, remaining)
        await asyncio.sleep(interval)
        remaining -= interval
