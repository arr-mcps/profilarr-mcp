"""MCP server exposing Profilarr's v1 REST API (https://dictionarry.dev) as tools.

One tool per /api/v1 endpoint (see README for the full list). Auth is an API
key sent as `X-Api-Key: <key>` on every request -- either the PROFILARR_API_KEY
env var (>=32 chars, takes precedence) or the bcrypt-stored key generated under
Profilarr Settings > Security.

Profilarr v1 only exposes a small, documented JSON surface (system, arr list,
databases CRUD + sync, job polling, backups, announcements). The bulk of
Profilarr's features (custom formats, quality profiles, regex, upgrades, rename,
notifications, arr-sync triggering) are SvelteKit form actions behind browser
session + CSRF, and are intentionally NOT wrapped here.

Binary backup endpoints are bridged to the local filesystem: download writes a
sanitized .tar.gz to a temp dir and returns the path; upload reads a local file
and POSTs it as multipart. Async endpoints (POST /backups, POST /databases/{id}/sync)
return {jobId} with HTTP 202; callers poll GET /jobs/{id} via profilarr_get_job.
"""

import os
import re
import sys
import tempfile
from typing import Any
from urllib.parse import quote

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

DESTRUCTIVE = ToolAnnotations(destructiveHint=True)

# Profilarr responses are plain JSON. `dict[str, Any]` (not bare `Any`) matters;
# FastMCP needs a concrete schema to build MCP structured content, and skips that
# step entirely for an `Any` return type -- which silently makes Client.call_tool's
# `.data` come back None for array-returning endpoints. See dashy-mcp for details.
JSONObj = dict[str, Any]
JSONVal = JSONObj | list[Any]

mcp = FastMCP("profilarr-mcp")

_client: httpx.AsyncClient | None = None
TEMP_DIR = tempfile.gettempdir()  # overridable via PROFILARR_TEMP_DIR

# Server rejects filenames not matching this whitelist; used to derive a safe
# local name for uploaded files before handing them back to the caller.
_BACKUP_RE = re.compile(r"^backup-[\w.-]+\.tar\.gz$")


def build_client(
    base_url: str, api_key: str | None, transport: httpx.BaseTransport | None = None
) -> httpx.AsyncClient:
    headers = {"X-Api-Key": api_key} if api_key else {}
    return httpx.AsyncClient(
        base_url=f"{base_url.rstrip('/')}/api/v1", headers=headers, transport=transport
    )


async def _req(method: str, path: str, json: Any = None) -> JSONVal:
    """JSON request; raises ToolError on >=400. 204 -> {}."""
    assert _client is not None, "client not configured"
    r = await _client.request(method, path, json=json)
    if r.status_code >= 400:
        raise _error_from(r)
    if r.status_code == 204:
        return {}
    return r.json()


async def _req_raw(path: str) -> httpx.Response:
    """Raw (binary) GET; caller must read body. Raises ToolError on >=400."""
    assert _client is not None, "client not configured"
    r = await _client.request("GET", path)
    if r.status_code >= 400:
        raise _error_from(r)
    return r


async def _req_upload(path: str, filepath: str) -> JSONVal:
    """Multipart upload of a local file."""
    assert _client is not None, "client not configured"
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        files = {"file": (filename, f, "application/gzip")}
        r = await _client.request("POST", path, files=files)
    if r.status_code >= 400:
        raise _error_from(r)
    return r.json()


def _error_from(r: httpx.Response) -> ToolError:
    """Build a ToolError from a Profilarr error response ({error: ...} or text)."""
    try:
        msg = r.json().get("error", r.text)
    except ValueError:
        msg = r.text
    return ToolError(f"Profilarr API {r.status_code}: {msg}")


def _f(filename: str) -> str:
    return quote(filename)


# --- System -----------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_health() -> JSONObj:
    """Check system health. Public -- requires no API key. Returns status
    (healthy|degraded|unhealthy) and timestamp."""
    return await _req("GET", "/health")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_status() -> JSONObj:
    """Get overall system status: version, uptime, databases, arrs (with sync
    state), job queue, backups, and unread announcements."""
    return await _req("GET", "/status")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_get_openapi_spec() -> JSONObj:
    """Fetch Profilarr's resolved OpenAPI 3.1 spec as a JSON document."""
    return await _req("GET", "/openapi.json")


# --- Arr --------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_list_arr_instances() -> list[Any]:
    """List connected Radarr/Sonarr instances. Secrets (api_key) are stripped."""
    return await _req("GET", "/arr")


# --- Databases --------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_list_databases() -> list[Any]:
    """List linked PCD databases. Secrets (personal_access_token -> hasPat) stripped."""
    return await _req("GET", "/databases")


@mcp.tool
async def profilarr_create_database(
    name: str,
    repository_url: str,
    branch: str | None = None,
    personal_access_token: str | None = None,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
    sync_strategy: int = 0,
    auto_pull: bool = False,
    local_ops_enabled: bool = False,
    conflict_strategy: str = "override",
) -> JSONObj:
    """Link a PCD database: clones the repo, validates its manifest, processes
    dependencies. Synchronous; may take seconds. `personal_access_token` requires
    `git_user_name` and `git_user_email`. Returns the new DatabaseInstance."""
    body: dict[str, Any] = {
        "name": name,
        "repository_url": repository_url,
        "sync_strategy": sync_strategy,
        "auto_pull": auto_pull,
        "local_ops_enabled": local_ops_enabled,
        "conflict_strategy": conflict_strategy,
    }
    if branch is not None:
        body["branch"] = branch
    if personal_access_token is not None:
        body["personal_access_token"] = personal_access_token
    if git_user_name is not None:
        body["git_user_name"] = git_user_name
    if git_user_email is not None:
        body["git_user_email"] = git_user_email
    return await _req("POST", "/databases", body)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_get_database(id: int) -> JSONObj:
    """Get a single linked database by id. Secrets stripped."""
    return await _req("GET", f"/databases/{id}")


@mcp.tool
async def profilarr_update_database(id: int, patch: dict) -> JSONObj:
    """Partial update of a linked database. Only provided fields change; unknown
    fields are ignored. See profilarr_create_database for field-dependency rules
    (PAT requires git_user_name/email, local_ops_enabled requires PAT)."""
    return await _req("PATCH", f"/databases/{id}", patch)


@mcp.tool(annotations=DESTRUCTIVE)
async def profilarr_delete_database(id: int) -> JSONObj:
    """Unlink a database: removes the row, deletes the cloned repo from disk,
    cancels scheduled sync jobs."""
    return await _req("DELETE", f"/databases/{id}")


@mcp.tool
async def profilarr_sync_database(id: int) -> JSONObj:
    """Trigger a database pull (PCD sync). Async: returns {jobId} (HTTP 202);
    poll with profilarr_get_job(jobId). Note: this pulls the linked DB repo,
    NOT an Arr instance sync."""
    return await _req("POST", f"/databases/{id}/sync")


# --- Jobs -------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_get_job(id: int) -> JSONObj:
    """Get current status of a job-queue entry (e.g. from profilarr_create_backup
    or profilarr_sync_database). Returns status with run result when finished."""
    return await _req("GET", f"/jobs/{id}")


# --- Backups ----------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_list_backups() -> list[Any]:
    """List backup archives on the server, newest first."""
    return await _req("GET", "/backups")


@mcp.tool
async def profilarr_create_backup() -> JSONObj:
    """Create a backup. Async: returns {jobId} (HTTP 202); poll with
    profilarr_get_job(jobId). The archive is sanitized (secrets stripped)."""
    return await _req("POST", "/backups")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_download_backup(filename: str) -> JSONObj:
    """Download a backup archive (sanitized on the fly) and write it to the local
    temp dir (PROFILARR_TEMP_DIR or system temp). Returns {path, filename, size}
    so the caller can read the file from disk."""
    r = await _req_raw(f"/backups/{_f(filename)}")
    local = os.path.join(TEMP_DIR, os.path.basename(filename))
    data = await r.aread()
    with open(local, "wb") as f:
        f.write(data)
    return {"path": local, "filename": os.path.basename(filename), "size": len(data)}


@mcp.tool(annotations=DESTRUCTIVE)
async def profilarr_delete_backup(filename: str) -> JSONObj:
    """Delete a backup archive from the server."""
    return await _req("DELETE", f"/backups/{_f(filename)}")


@mcp.tool
async def profilarr_upload_backup(filepath: str) -> JSONObj:
    """Upload a local .tar.gz backup to the server as multipart form data. Returns
    the stored filename/size."""
    return await _req_upload("/backups/upload", filepath)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_get_backup_settings() -> JSONObj:
    """Get backup settings (schedule, retentionDays, enabled, ...)."""
    return await _req("GET", "/backups/settings")


@mcp.tool
async def profilarr_update_backup_settings(
    schedule: str | None = None,
    retention_days: int | None = None,
    enabled: bool | None = None,
) -> JSONObj:
    """Update backup settings. Only schedule (hourly|daily|weekly|monthly),
    retention_days (1-365), and enabled are updatable. Returns updated settings."""
    body: dict[str, Any] = {}
    if schedule is not None:
        body["schedule"] = schedule
    if retention_days is not None:
        body["retentionDays"] = retention_days
    if enabled is not None:
        body["enabled"] = enabled
    if not body:
        raise ToolError("Profilarr API 400: no updatable fields provided")
    return await _req("PATCH", "/backups/settings", body)


# --- Announcements ----------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_list_announcements() -> list[Any]:
    """List visible announcements (withdrawn/expired/version-incompatible filtered),
    newest first."""
    return await _req("GET", "/announcements")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def profilarr_get_announcement(id: str) -> JSONObj:
    """Get a single announcement by id, including its markdown body."""
    return await _req("GET", f"/announcements/{_f(id)}")


def main() -> None:
    global _client, TEMP_DIR
    url = os.environ.get("PROFILARR_URL")
    if not url:
        print("PROFILARR_URL environment variable is required (e.g. https://profilarr.example.com)", file=sys.stderr)
        raise SystemExit(1)
    TEMP_DIR = os.environ.get("PROFILARR_TEMP_DIR", tempfile.gettempdir())
    _client = build_client(url, os.environ.get("PROFILARR_API_KEY"))
    mcp.run()


if __name__ == "__main__":
    main()