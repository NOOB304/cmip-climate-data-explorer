import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from cmip_explorer.application.workflow import _http_download_candidates
from cmip_explorer.domain.models import AccessEndpoint, LogicalFile, Replica
from cmip_explorer.infrastructure.download import DownloadControl, HttpRangeDownloader


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
