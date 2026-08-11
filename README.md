# profilarr-mcp

MCP server exposing [Profilarr](https://github.com/Dictionarry-Hub/Profilarr)'s
v1 REST API (`/api/v1`, [OpenAPI 3.1](https://dictionarry.dev)) as tools, so an
LLM can inspect and manage your Profilarr instance: linked databases, connected
Radarr/Sonarr instances, backups, jobs, announcements, and system status.

Built with [FastMCP](https://gofastmcp.com).

## Scope

Profilarr's programmatic API is deliberately small. This server wraps the
documented `/api/v1` JSON surface only:

- **System** — health, status, OpenAPI spec
- **Arr** — list connected instances (read-only; sync state via status)
- **Databases** — link/get/update/unlink, trigger PCD sync
- **Jobs** — poll job status
- **Backups** — list/create/download/upload/delete/settings
- **Announcements** — list/get

Many of Profilarr's headline features (custom formats, quality profiles,
regular expressions, delay profiles, media management, upgrades, rename,
notifications, and triggering an Arr *sync*) are **not** exposed by the v1 REST
API — they live behind SvelteKit form actions that require a browser session and
CSRF token, so they are intentionally not wrapped here. Use the web UI for those.

## Getting the API key

Profilarr accepts `X-Api-Key` on every `/api/v1` request. The active key is
either the `PROFILARR_API_KEY` environment variable set on the Profilarr server
(≥ 32 characters, takes precedence), or the key generated under
**Settings > Security** in the web UI (bcrypt-stored, shown once). `/health` is
the only endpoint that works without a key.

## Install

Download a wheel from the [latest release](https://github.com/SavageCore/profilarr-mcp/releases/latest)
and install it as a `uv` tool (no repo checkout needed):

```bash
uv tool install profilarr_mcp-*.whl
```

This puts a `profilarr-mcp` command on your PATH. Register it with Claude Code:

```bash
claude mcp add profilarr \
  --env PROFILARR_URL=https://your-profilarr-host \
  --env PROFILARR_API_KEY=<key> \
  -- profilarr-mcp
```

### From source

```bash
uv sync
cp .env.example .env   # fill in PROFILARR_URL and PROFILARR_API_KEY
```

```bash
claude mcp add profilarr \
  --env PROFILARR_URL=https://your-profilarr-host \
  --env PROFILARR_API_KEY=<key> \
  -- uv run --directory /path/to/profilarr-mcp profilarr-mcp
```

## Config

| Env var | Required | Default |
|---|---|---|
| `PROFILARR_URL` | yes | - |
| `PROFILARR_API_KEY` | yes* | none (no auth header sent) |
| `PROFILARR_TEMP_DIR` | no | system temp dir |

*Required for every tool except `profilarr_health`, which is public.

## Tools

One tool per Profilarr v1 endpoint:

| Tool | Endpoint |
|---|---|
| `profilarr_health` | `GET /health` |
| `profilarr_status` | `GET /status` |
| `profilarr_get_openapi_spec` | `GET /openapi.json` |
| `profilarr_list_arr_instances` | `GET /arr` |
| `profilarr_list_databases` | `GET /databases` |
| `profilarr_create_database` | `POST /databases` |
| `profilarr_get_database` | `GET /databases/{id}` |
| `profilarr_update_database` | `PATCH /databases/{id}` |
| `profilarr_delete_database` | `DELETE /databases/{id}` |
| `profilarr_sync_database` | `POST /databases/{id}/sync` |
| `profilarr_get_job` | `GET /jobs/{id}` |
| `profilarr_list_backups` | `GET /backups` |
| `profilarr_create_backup` | `POST /backups` |
| `profilarr_download_backup` | `GET /backups/{filename}` |
| `profilarr_delete_backup` | `DELETE /backups/{filename}` |
| `profilarr_upload_backup` | `POST /backups/upload` |
| `profilarr_get_backup_settings` | `GET /backups/settings` |
| `profilarr_update_backup_settings` | `PATCH /backups/settings` |
| `profilarr_list_announcements` | `GET /announcements` |
| `profilarr_get_announcement` | `GET /announcements/{id}` |

### Async jobs

`profilarr_create_backup` and `profilarr_sync_database` return `{jobId}` with
HTTP 202. Poll the result with `profilarr_get_job(jobId)`. Note
`profilarr_sync_database` pulls the *linked database repo*, not an Arr sync.

### Binary endpoints

`profilarr_download_backup` streams the sanitized archive to a local temp file
(`PROFILARR_TEMP_DIR` or the system temp dir) and returns `{path, filename, size}`.
`profilarr_upload_backup` takes a local file path and POSTs it as multipart form
data. Bytes never enter the LLM context.

## Development

```bash
make help  # list all commands
```

| Command | Does |
|---|---|
| `make sync` | `uv sync` |
| `make test` | Offline tests - one per endpoint, mocked HTTP |
| `make test-integration` | Tests against the live instance (needs `PROFILARR_URL`/`PROFILARR_API_KEY`) |
| `make build` | Build wheel + sdist into `dist/` |
| `make bump-patch` / `bump-minor` / `bump-major` | Bump the version in `pyproject.toml` + `uv.lock` |
| `make clean` | Remove build artifacts |

The release workflow (`.github/workflows/release.yml`) builds and publishes to
[Releases](https://github.com/SavageCore/profilarr-mcp/releases) whenever a `v*`
tag is pushed — so the usual flow is `make bump-patch`, commit, then tag and push.

The integration suite is read-only (health/status/arr/databases/announcements)
plus a self-cleaning backup lifecycle (create → poll → download → delete).
Database create/delete is intentionally not exercised against a live instance,
since linking a database clones a real repository.
