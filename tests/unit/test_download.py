import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from cmip_explorer.application.workflow import _http_download_candidates
from cmip_explorer.domain.models import AccessEndpoint, LogicalFile, Replica
from cmip_explorer.infrastructure.download import DownloadControl, HttpRangeDownloader


class _InterruptedStream(httpx.AsyncByteStream):
    def __init__(self, request: httpx.Request, first_chunk: bytes) -> None:
        self.request = request
        self.first_chunk = first_chunk

    async def __aiter__(self):
        yield self.first_chunk
        raise httpx.ReadError("connection reset", request=self.request)


def test_download_candidates_prefer_https_and_exclude_plain_http() -> None:
    file = LogicalFile(
        logical_key="test",
        filename="test.nc",
        replicas=(
            Replica(
                data_node="node-a",
                backend_id="test",
                replica=False,
                endpoints=(
                    AccessEndpoint(
                        url="http://node-a.test/file.nc",
                        service="HTTPServer",
                    ),
                    AccessEndpoint(
                        url="https://node-b.test/file.nc",
                        service="HTTPServer",
                        secure=True,
                    ),
                ),
            ),
        ),
    )
    assert _http_download_candidates(file, False) == (
        "https://node-b.test/file.nc",
        "https://node-a.test/file.nc",
    )
    assert _http_download_candidates(file, True)[-1] == "http://node-a.test/file.nc"


def test_checksum_allows_verified_http_fallback() -> None:
    file = LogicalFile(
        logical_key="verified",
        filename="verified.nc",
        replicas=(
            Replica(
                data_node="legacy.test",
                backend_id="test",
                replica=False,
                checksum="abc123",
                checksum_type="MD5",
                endpoints=(
                    AccessEndpoint(
                        url="http://legacy.test/verified.nc",
                        service="HTTPServer",
                    ),
                ),
            ),
        ),
    )
    assert _http_download_candidates(file, False) == (
        "https://legacy.test/verified.nc",
        "http://legacy.test/verified.nc",
    )


async def test_md5_validation_still_reports_sha256(tmp_path: Path) -> None:
    payload = b"climate-data"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(payload))})
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    downloader = HttpRangeDownloader(client=client, chunk_size=4)
    try:
        result = await downloader.download(
            "https://example.test/file.nc",
            tmp_path / "file.nc",
            expected_size=len(payload),
            expected_checksum=hashlib.md5(payload).hexdigest(),
            checksum_type="MD5",
        )
    finally:
        await client.aclose()
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.path.read_bytes() == payload


async def test_paused_download_continues_after_resume(tmp_path: Path) -> None:
    payload = b"regional-subset"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(payload))})
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    downloader = HttpRangeDownloader(client=client, chunk_size=4)
    control = DownloadControl(pause_requested=True)

    async def resume() -> None:
        await asyncio.sleep(0.05)
        control.pause_requested = False

    resume_task = asyncio.create_task(resume())
    try:
        result = await downloader.download(
            "https://example.test/file.nc",
            tmp_path / "resumed.nc",
            expected_size=len(payload),
            control=control,
        )
        await resume_task
    finally:
        await client.aclose()
    assert result.path.read_bytes() == payload


async def test_range_download_resumes_matching_partial_file(tmp_path: Path) -> None:
    payload = b"regional-subset"
    url = "https://example.test/file.nc"
    target = tmp_path / "file.nc"
    part = target.with_suffix(".nc.part")
    sidecar = target.with_suffix(".nc.part.json")
    part.write_bytes(payload[:4])
    sidecar.write_text(
        json.dumps(
            {
                "source_url": url,
                "etag": "test-etag",
                "last_modified": None,
                "expected_size": len(payload),
            }
        ),
        encoding="utf-8",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        headers = {"Content-Length": str(len(payload)), "ETag": "test-etag"}
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        assert request.headers["Range"] == "bytes=4-"
        return httpx.Response(206, content=payload[4:])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    downloader = HttpRangeDownloader(client=client, chunk_size=4)
    try:
        result = await downloader.download(url, target, expected_size=len(payload))
    finally:
        await client.aclose()
    assert result.resumed_from == 4
    assert target.read_bytes() == payload


async def test_interrupted_download_auto_reconnects_from_partial_file(
    tmp_path: Path,
) -> None:
    payload = b"update-installer-with-resume"
    first_chunk = payload[:8]
    get_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_calls
        headers = {"Content-Length": str(len(payload)), "ETag": "release-asset"}
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        get_calls += 1
        if get_calls == 1:
            return httpx.Response(
                200,
                headers=headers,
                stream=_InterruptedStream(request, first_chunk),
            )
        assert request.headers["Range"] == f"bytes={len(first_chunk)}-"
        return httpx.Response(206, content=payload[len(first_chunk) :])

    reconnects: list[tuple[int, int, float]] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await HttpRangeDownloader(
            client=client,
            chunk_size=4,
            reconnect_delays=(0,),
        ).download(
            "https://example.test/update.exe",
            tmp_path / "update.exe",
            expected_size=len(payload),
            reconnect=lambda attempt, maximum, delay, _error: reconnects.append(
                (attempt, maximum, delay)
            ),
        )

    assert result.path.read_bytes() == payload
    assert result.resumed_from == len(first_chunk)
    assert reconnects == [(1, 1, 0)]


async def test_large_download_is_split_into_bounded_range_requests(tmp_path: Path) -> None:
    payload = b"abcdefghijklmnopqrstuvwxyz"
    ranges: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"Content-Length": str(len(payload)), "ETag": "segmented"},
            )
        value = request.headers["Range"]
        ranges.append(value)
        start_text, end_text = value.removeprefix("bytes=").split("-")
        start, end = int(start_text), int(end_text)
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes {start}-{end}/{len(payload)}"},
            content=payload[start : end + 1],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await HttpRangeDownloader(
            client=client,
            chunk_size=4,
            request_chunk_bytes=8,
        ).download(
            "https://example.test/large.bin",
            tmp_path / "large.bin",
            expected_size=len(payload),
            expected_checksum=hashlib.sha256(payload).hexdigest(),
        )

    assert result.path.read_bytes() == payload
    assert ranges == ["bytes=0-7", "bytes=8-15", "bytes=16-23", "bytes=24-25"]


async def test_checksum_verified_download_resumes_when_cdn_etag_changes(
    tmp_path: Path,
) -> None:
    payload = b"immutable-release-asset"
    url = "https://example.test/release.exe"
    target = tmp_path / "release.exe"
    part = target.with_suffix(".exe.part")
    sidecar = target.with_suffix(".exe.part.json")
    part.write_bytes(payload[:9])
    sidecar.write_text(
        json.dumps(
            {
                "source_url": url,
                "etag": "old-cdn-etag",
                "last_modified": None,
                "expected_size": len(payload),
            }
        ),
        encoding="utf-8",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"Content-Length": str(len(payload)), "ETag": "new-cdn-etag"},
            )
        assert request.headers["Range"] == "bytes=9-"
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes 9-{len(payload) - 1}/{len(payload)}"},
            content=payload[9:],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await HttpRangeDownloader(client=client).download(
            url,
            target,
            expected_size=len(payload),
            expected_checksum=hashlib.sha256(payload).hexdigest(),
        )

    assert result.resumed_from == 9
    assert target.read_bytes() == payload


async def test_checksum_verified_partial_resumes_from_another_mirror(
    tmp_path: Path,
) -> None:
    payload = b"same-verified-file-on-two-mirrors"
    target = tmp_path / "mirror.nc"
    part = target.with_suffix(".nc.part")
    sidecar = target.with_suffix(".nc.part.json")
    part.write_bytes(payload[:8])
    sidecar.write_text(
        json.dumps(
            {
                "source_url": "https://slow.example.test/data/mirror.nc",
                "etag": "slow-node",
                "last_modified": None,
                "expected_size": len(payload),
            }
        ),
        encoding="utf-8",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"Content-Length": str(len(payload)), "ETag": "fast-node"},
            )
        assert request.url.host == "fast.example.test"
        assert request.headers["Range"] == "bytes=8-"
        return httpx.Response(206, content=payload[8:])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await HttpRangeDownloader(client=client).download(
            "https://fast.example.test/data/mirror.nc",
            target,
            expected_size=len(payload),
            expected_checksum=hashlib.sha256(payload).hexdigest(),
        )

    assert result.resumed_from == 8
    assert target.read_bytes() == payload


async def test_probe_speed_distinguishes_fast_and_slow_candidates() -> None:
    payload = b"x" * (16 * 1024)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "slow.example.test":
            await asyncio.sleep(0.04)
        return httpx.Response(206, content=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = HttpRangeDownloader(client=client)
        fast, slow = await asyncio.gather(
            downloader.probe_speed("https://fast.example.test/data.nc"),
            downloader.probe_speed("https://slow.example.test/data.nc"),
        )

    assert fast > slow


async def test_resume_keeps_partial_when_only_url_scheme_changes(tmp_path: Path) -> None:
    payload = b"scheme-compatible-resume"
    target = tmp_path / "scheme.nc"
    part = target.with_suffix(".nc.part")
    sidecar = target.with_suffix(".nc.part.json")
    part.write_bytes(payload[:7])
    sidecar.write_text(
        json.dumps(
            {
                "source_url": "https://example.test/scheme.nc",
                "etag": "same-file",
                "last_modified": None,
                "expected_size": len(payload),
            }
        ),
        encoding="utf-8",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        headers = {"Content-Length": str(len(payload)), "ETag": "same-file"}
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        assert request.headers["Range"] == "bytes=7-"
        return httpx.Response(206, content=payload[7:])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await HttpRangeDownloader(client=client).download(
            "http://example.test/scheme.nc", target, expected_size=len(payload)
        )
    finally:
        await client.aclose()
    assert result.resumed_from == 7
    assert target.read_bytes() == payload


def test_default_downloader_can_run_on_two_separate_event_loops(
    tmp_path: Path, monkeypatch
) -> None:
    payload = b"two-consecutive-downloads"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(payload))})
        return httpx.Response(200, content=payload)

    downloader = HttpRangeDownloader(chunk_size=4)
    monkeypatch.setattr(
        downloader,
        "_new_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    first = asyncio.run(downloader.download("https://example.test/first.nc", tmp_path / "first.nc"))
    second = asyncio.run(
        downloader.download("https://example.test/second.nc", tmp_path / "second.nc")
    )
    assert first.path.read_bytes() == payload
    assert second.path.read_bytes() == payload
