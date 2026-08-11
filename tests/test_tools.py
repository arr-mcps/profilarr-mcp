"""Offline tests: one per Profilarr v1 API endpoint, plus error/edge paths.

No network. Each tool call is checked against the exact HTTP request it should
produce (method, path incl. URL-encoding, JSON body, auth header) via
httpx.MockTransport, using FastMCP's in-memory Client (see
https://gofastmcp.com/development/tests).
"""

import json
import os

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import profilarr_mcp


class Recorder:
    """Captures the single request made during a test and replays a canned response."""

    def __init__(self):
        self.method = None
        self.url = None
        self.headers = None
        self.json = None
        self.content = None
        self.response = httpx.Response(200, json={"success": True})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.method = request.method
        self.url = request.url
        self.headers = request.headers
        self.content = request.content
        try:
            self.json = json.loads(request.content) if request.content else None
        except ValueError:
            self.json = None
        return self.response


@pytest.fixture
def recorder():
    return Recorder()


@pytest_asyncio.fixture
async def server(recorder, monkeypatch):
    transport = httpx.MockTransport(recorder.handler)
    client = profilarr_mcp.build_client("https://profilarr.example.com", "test-key", transport=transport)
    monkeypatch.setattr(profilarr_mcp, "_client", client)
    monkeypatch.setattr(profilarr_mcp, "TEMP_DIR", "/tmp")
    yield profilarr_mcp.mcp
    await client.aclose()


async def call(server, tool, **kwargs):
    async with Client(server) as c:
        return await c.call_tool(tool, kwargs)


# --- System ----------------------------------------------------------------

async def test_1_health(server, recorder):
    recorder.response = httpx.Response(200, json={"status": "healthy", "timestamp": "2026-01-01T00:00:00Z"})
    result = await call(server, "profilarr_health")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/health"
    assert result.data["status"] == "healthy"


async def test_2_status(server, recorder):
    recorder.response = httpx.Response(200, json={"version": "2.0.0", "uptime": 1})
    result = await call(server, "profilarr_status")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/status"
    assert result.data["version"] == "2.0.0"


async def test_3_openapi_spec(server, recorder):
    recorder.response = httpx.Response(200, json={"openapi": "3.1.0"})
    result = await call(server, "profilarr_get_openapi_spec")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/openapi.json"
    assert result.data["openapi"] == "3.1.0"


# --- Arr -------------------------------------------------------------------

async def test_4_list_arr_instances(server, recorder):
    recorder.response = httpx.Response(200, json=[{"id": 1, "name": "Radarr"}])
    result = await call(server, "profilarr_list_arr_instances")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/arr"
    assert result.data == [{"id": 1, "name": "Radarr"}]


# --- Databases -------------------------------------------------------------

async def test_5_list_databases(server, recorder):
    recorder.response = httpx.Response(200, json=[{"id": 1, "name": "Dictionarry"}])
    result = await call(server, "profilarr_list_databases")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/databases"
    assert result.data[0]["name"] == "Dictionarry"


async def test_6_create_database(server, recorder):
    recorder.response = httpx.Response(201, json={"id": 1, "name": "Dictionarry"})
    await call(server, "profilarr_create_database", name="Dictionarry", repository_url="https://github.com/Dictionarry-Hub/database")
    assert recorder.method == "POST"
    assert recorder.url.path == "/api/v1/databases"
    assert recorder.json["name"] == "Dictionarry"
    assert recorder.json["conflict_strategy"] == "override"


async def test_7_create_database_omits_optional_none(server, recorder):
    await call(server, "profilarr_create_database", name="X", repository_url="https://x")
    assert "branch" not in recorder.json
    assert "personal_access_token" not in recorder.json


async def test_8_get_database(server, recorder):
    await call(server, "profilarr_get_database", id=3)
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/databases/3"


async def test_9_update_database(server, recorder):
    patch = {"name": "Renamed"}
    await call(server, "profilarr_update_database", id=3, patch=patch)
    assert recorder.method == "PATCH"
    assert recorder.url.path == "/api/v1/databases/3"
    assert recorder.json == patch


async def test_10_delete_database_returns_empty_for_204(server, recorder):
    recorder.response = httpx.Response(204)
    result = await call(server, "profilarr_delete_database", id=3)
    assert recorder.method == "DELETE"
    assert recorder.url.path == "/api/v1/databases/3"
    assert result.content[0].text == "{}"


async def test_11_sync_database(server, recorder):
    recorder.response = httpx.Response(202, json={"jobId": 42})
    result = await call(server, "profilarr_sync_database", id=3)
    assert recorder.method == "POST"
    assert recorder.url.path == "/api/v1/databases/3/sync"
    assert result.data == {"jobId": 42}


# --- Jobs ------------------------------------------------------------------

async def test_12_get_job(server, recorder):
    await call(server, "profilarr_get_job", id=42)
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/jobs/42"


# --- Backups ---------------------------------------------------------------

async def test_13_list_backups(server, recorder):
    recorder.response = httpx.Response(200, json=[{"filename": "backup-1.tar.gz"}])
    result = await call(server, "profilarr_list_backups")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/backups"
    assert result.data[0]["filename"] == "backup-1.tar.gz"


async def test_14_create_backup(server, recorder):
    recorder.response = httpx.Response(202, json={"jobId": 7})
    result = await call(server, "profilarr_create_backup")
    assert recorder.method == "POST"
    assert recorder.url.path == "/api/v1/backups"
    assert result.data == {"jobId": 7}


async def test_15_download_backup_writes_temp_file(server, recorder):
    recorder.response = httpx.Response(
        200,
        content=b"tar-bytes",
        headers={"content-disposition": 'attachment; filename="backup-test.tar.gz"'},
    )
    result = await call(server, "profilarr_download_backup", filename="backup-test.tar.gz")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/backups/backup-test.tar.gz"
    assert result.data["path"] == "/tmp/backup-test.tar.gz"
    assert result.data["size"] == 9


async def test_16_delete_backup(server, recorder):
    recorder.response = httpx.Response(200, json={"success": True})
    await call(server, "profilarr_delete_backup", filename="backup-test.tar.gz")
    assert recorder.method == "DELETE"
    assert recorder.url.path == "/api/v1/backups/backup-test.tar.gz"


async def test_17_upload_backup_sends_multipart(server, recorder, tmp_path):
    f = tmp_path / "backup-upload.tar.gz"
    f.write_bytes(b"tar-bytes")
    recorder.response = httpx.Response(201, json={"filename": "backup-upload.tar.gz", "size": 10})
    result = await call(server, "profilarr_upload_backup", filepath=str(f))
    assert recorder.method == "POST"
    assert recorder.url.path == "/api/v1/backups/upload"
    assert "multipart/form-data" in recorder.headers["content-type"]
    assert b"tar-bytes" in recorder.content
    assert result.data["filename"] == "backup-upload.tar.gz"


async def test_18_get_backup_settings(server, recorder):
    recorder.response = httpx.Response(200, json={"schedule": "daily"})
    result = await call(server, "profilarr_get_backup_settings")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/backups/settings"
    assert result.data["schedule"] == "daily"


async def test_19_update_backup_settings(server, recorder):
    recorder.response = httpx.Response(200, json={"schedule": "weekly", "enabled": True})
    result = await call(server, "profilarr_update_backup_settings", schedule="weekly", enabled=True)
    assert recorder.method == "PATCH"
    assert recorder.url.path == "/api/v1/backups/settings"
    assert recorder.json == {"schedule": "weekly", "enabled": True}
    assert result.data["schedule"] == "weekly"


async def test_20_update_backup_settings_empty_raises(server, recorder):
    with pytest.raises(ToolError):
        await call(server, "profilarr_update_backup_settings")


# --- Announcements ---------------------------------------------------------

async def test_21_list_announcements(server, recorder):
    recorder.response = httpx.Response(200, json=[{"id": "abc", "title": "Hi"}])
    result = await call(server, "profilarr_list_announcements")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/announcements"
    assert result.data[0]["title"] == "Hi"


async def test_22_get_announcement(server, recorder):
    await call(server, "profilarr_get_announcement", id="abc")
    assert recorder.method == "GET"
    assert recorder.url.path == "/api/v1/announcements/abc"


# --- auth header ------------------------------------------------------------

async def test_api_key_sent_as_header(server, recorder):
    recorder.response = httpx.Response(200, json=[{"id": 1}])
    await call(server, "profilarr_list_arr_instances")
    assert recorder.headers["x-api-key"] == "test-key"


async def test_no_key_means_no_auth_header(recorder, monkeypatch):
    transport = httpx.MockTransport(recorder.handler)
    client = profilarr_mcp.build_client("https://profilarr.example.com", None, transport=transport)
    monkeypatch.setattr(profilarr_mcp, "_client", client)
    await call(profilarr_mcp.mcp, "profilarr_health")
    assert "x-api-key" not in recorder.headers
    await client.aclose()


# --- error paths --------------------------------------------------------------

async def test_error_json_surfaces_message(server, recorder):
    recorder.response = httpx.Response(404, json={"error": "Database not found"})
    with pytest.raises(ToolError, match="Database not found"):
        await call(server, "profilarr_get_database", id=99)


async def test_401_error_surfaces_status(server, recorder):
    recorder.response = httpx.Response(401, json={"error": "Unauthorized"})
    with pytest.raises(ToolError, match="401"):
        await call(server, "profilarr_list_backups")


async def test_non_json_error_body_does_not_crash(server, recorder):
    recorder.response = httpx.Response(502, text="<html>Bad Gateway</html>")
    with pytest.raises(ToolError, match="502"):
        await call(server, "profilarr_list_arr_instances")


# --- main() -----------------------------------------------------------------

def test_main_requires_profilarr_url(monkeypatch):
    monkeypatch.delenv("PROFILARR_URL", raising=False)
    with pytest.raises(SystemExit):
        profilarr_mcp.main()
