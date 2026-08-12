# AGENTS.md — profilarr-mcp

MCP server exposing Profilarr's v1 REST API (`/api/v1`) as tools so an LLM can inspect and manage a Profilarr instance: linked databases, connected Radarr/Sonarr instances, backups, jobs, announcements, and system status. Uses FastMCP, `uv` for deps.

Exposed as **4 resource-scoped portmanteau tools**, not one tool per endpoint — see "Portmanteau registration" below. A prior version registered all 20 endpoints individually.

## Testing
- Offline suite: `make test` (or `uv run pytest`)
- Live integration (needs `PROFILARR_URL`/`PROFILARR_API_KEY`): `make test-integration`

## Release workflow
Always use the `make bump-*` targets to bump the version (`uv version --bump patch|minor|major`), which updates `pyproject.toml` and `uv.lock` together. Do NOT edit the version by hand.

- Bump: `make bump-patch` (or `bump-minor` / `bump-major`)
- Commit message is **just the version**, e.g. `0.1.2` — nothing else.
- Tag it `v<version>` (e.g. `v0.1.2`).
- Push main and the tag:
  ```
  git push origin main
  git push origin v<version>
  ```
- Then sync the project copy:
  ```
  cd /home/savagecore/Documents/christopfarr/mcp/profilarr-mcp
  git fetch origin && git reset --hard origin/main
  ```
- Deploy to the Proxmox host (root SSH key): pull the repo then reinstall the uv tool:
  ```
  ssh root@192.168.50.3 -- 'cd /root/profilarr-mcp && git fetch origin && git reset --hard origin/main'
  ssh root@192.168.50.3 -- 'cd /root/profilarr-mcp && uv tool install --force .'
  ```
  The host runs it via `uv tool install` → `/root/.local/bin/profilarr-mcp` (not from the repo).

## Env note
Profilarr's v1 programmatic API is deliberately small. All tools are keyed on `PROFILARR_API_KEY`; keep API keys out of code, logs, and commit messages.

## Portmanteau registration — **do not go back to one tool per endpoint**
- `_GROUPS` near the bottom of `profilarr_mcp.py` buckets every endpoint function into one of 4 resource groups (`profilarr_databases`, `profilarr_backups`, `profilarr_jobs_arr`, `profilarr_meta`). `_register_tools()` registers exactly one MCP tool per group via `_register_group`, which wraps the group's functions in a single `dispatch(operation, arguments)` closure. The endpoint functions themselves are unchanged — they're plain callables looked up by name via `globals()`, not separately-registered tools.
- `operation` is typed `Literal[<the group's function names>]`, so FastMCP/pydantic validates it against the real operation list before `dispatch` ever runs — an invalid operation never reaches the group tool's body.
- Adding a new endpoint: write the function as before (no decorator), then add its name to exactly one group in `_GROUPS`. `tests/test_tools.py::test_all_operations_grouped` fails if a name doesn't resolve to a real module attribute.
- New resource area big enough to need its own group: add a new `_GROUPS` key. Keep the total group count at or under ~15.
- If you're tempted to add a per-endpoint `@mcp.tool` decorator back, don't — every endpoint must be reachable only via its group's `operation` enum.
- Annotations: a group tool is `readOnlyHint=True` (`READONLY` — a module constant added during the portmanteau refactor since the file previously only had a bare `DESTRUCTIVE` constant and used inline `ToolAnnotations(readOnlyHint=True)` per read-only tool) only when *every* operation in it was originally read-only. Mixed groups carry no hints.