"""Integration tests against a real Profilarr instance.

Skipped unless PROFILARR_URL and PROFILARR_API_KEY are set. Run with:
    uv run pytest -m integration

Read-only by design: no database create/delete here (linking a DB clones a real
repo and litters state). The only write is a self-cleaning backup lifecycle --
create a backup, poll the job, download it to a temp file, then delete it.
"""

import asyncio
import os

import pytest
from fastmcp import Client

import profilarr_mcp

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.environ.get("PROFILARR_URL") and os.environ.get("PROFILARR_API_KEY")),
        reason="requires PROFILARR_URL and PROFILARR_API_KEY",
    ),
]


@pytest.fixture(autouse=True)
def configure_client():
    profilarr_mcp._client = profilarr_mcp.build_client(os.environ["PROFILARR_URL"], os.environ["PROFILARR_API_KEY"])
    profilarr_mcp.TEMP_DIR = os.environ.get("PROFILARR_TEMP_DIR", "/tmp")
    yield


async def call(name, **kwargs):
    async with Client(profilarr_mcp.mcp) as c:
        return await c.call_tool(name, kwargs)


async def test_health_returns_status():
    result = await call("profilarr_health")
    assert result.data["status"] in ("healthy", "degraded", "unhealthy")


async def test_status_includes_dashboard_payload():
    result = await call("profilarr_status")
    for key in ("version", "databases", "arrs", "jobs", "backups", "announcements"):
        assert key in result.data


async def test_list_arr_instances_returns_list():
    result = await call("profilarr_list_arr_instances")
    assert isinstance(result.data, list)


async def test_list_databases_returns_list():
    result = await call("profilarr_list_databases")
    assert isinstance(result.data, list)


async def test_list_announcements_returns_list():
    result = await call("profilarr_list_announcements")
    assert isinstance(result.data, list)


async def test_backup_lifecycle():
    created = await call("profilarr_create_backup")
    job_id = created.data["jobId"]

    # poll the job to a terminal state
    for _ in range(20):
        job = await call("profilarr_get_job", id=job_id)
        if job.data["status"] in ("success", "failed", "cancelled"):
            break
        await asyncio.sleep(1)
    assert job.data["status"] == "success", job.data

    # find the backup we just made and delete it
    listings = await call("profilarr_list_backups")
    assert listings.data, "expected at least one backup"

    downloaded = await call("profilarr_download_backup", filename=listings.data[0]["filename"])
    assert os.path.exists(downloaded.data["path"])
    assert downloaded.data["size"] > 0
    os.remove(downloaded.data["path"])

    result = await call("profilarr_delete_backup", filename=listings.data[0]["filename"])
    assert result.data.get("success") is True
