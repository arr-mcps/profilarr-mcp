# AGENTS.md — profilarr-mcp

MCP server exposing Profilarr's v1 REST API (`/api/v1`) as tools so an LLM can inspect and manage a Profilarr instance: linked databases, connected Radarr/Sonarr instances, backups, jobs, announcements, and system status. Uses FastMCP, `uv` for deps.

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