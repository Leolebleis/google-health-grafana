# CLAUDE.md

Personal health-data pipeline: two collectors write to InfluxDB 3 Core on the
Raspberry Pi, visualized in Grafana (which runs in a separate stack, not this
repo -- `dashboard.json` / `scale-dashboard.json` are exports to import manually).

## Components

| Component | Code | Runs as | Writes |
|-----------|------|---------|--------|
| Google Health fetcher | `fetch.py` (single file) | Docker `health-fetch`, polls every 5 min | fitbit-grafana-compatible measurements (`weight`, `HeartRate_Intraday`, `Sleep Summary`, ...) |
| Xiaomi S400 scale reader | `scale/` package | systemd `scale-reader` on the Pi host (needs host Bluetooth, so not Docker) | `body_composition` measurement |
| Xiaomi cloud scale sync | `scale/cloud_sync.py` | systemd `scale-sync.timer` (hourly oneshot), runs SmartScaleConnect via docker | `body_composition` measurement |
| Hevy workout sync | `hevy/` package | systemd `hevy-sync.timer` (hourly oneshot), polls the official Hevy API | `workout` + `workout_set` measurements |

InfluxDB 3 Core runs in this repo's compose (`health-influxdb`, host port 8181,
`--without-auth` -- the token env vars are placeholders).

## Commands

```bash
uv sync                                  # Python 3.13, deps + dev group
uv run pytest --cov -v                   # 80% coverage gate
uv run ruff check scale/ hevy/ tests/
uv run ruff format --check scale/ hevy/ tests/
uv run ty check scale/ hevy/
```

CI runs exactly these. `fetch.py` is deliberately outside lint/type/test scope;
coverage also omits `scale/measurement/scanner_facade.py`.

## Architecture (scale/)

Layered: `scanner/` (bleak BLE + `s400_decrypt.py`, MiBeacon v5 AES-CCM) ->
`service.py` (dedup window) -> `calculator.py` (body composition from
weight + impedance + user profile) -> `persistence/` (mapper + writer).
Runtime config in `scale/config.yaml` (gitignored -- copy
`scale/config.example.yaml`).

## Gotchas

- **The fetch.py schema is fitbit-grafana compatible on purpose** -- measurement
  and field names match prebuilt dashboards; don't rename them.
- `InfluxDBClient3(host=...)` needs a full URL (`http://...:8181`), not host+port.
- Secrets are gitignored: `.env` (Google OAuth + Influx env), `tokens/`
  (OAuth refresh token), `scale/config.yaml` (scale bindkey).
- `docs/superpowers/` holds the original design/plan docs; the InfluxDB
  sections predate the v3 migration.
- **The scale's spot is out of BLE range of the Pi** -- the BLE reader only
  catches weigh-ins near the Pi. The cloud sync (`scale-sync`) is the primary
  weight path; readings arrive when the phone syncs the scale.
- Xiaomi cloud auth: password logins always trigger identity verification, so
  `scale-sync/scaleconnect.json` (gitignored) holds a long-lived passToken.
  If it expires, re-seed it from browser cookies on account.xiaomi.com
  (`userId` + `passToken`, written as `{"xiaomi:<email>": "<userId>:<passToken>"}`).
- Hevy API: key lives in `hevy-sync/env` (gitignored, requires Hevy Pro). The
  API has GET/POST/PUT but **no DELETE** -- deleting a workout must happen in
  the app, and hevy-sync won't remove already-synced points (drop the
  `workout`/`workout_set` tables to resync from scratch). The sync watermark is
  MAX(time) on the `workout` measurement. A `hevy-mcp` MCP server (user-scoped
  on the Windows machine) exposes the same API to Claude directly.
- Grafana dashboards: `dashboard.json` (Health), `scale-dashboard.json`,
  `training-dashboard.json` (Hevy data). Deploy via
  `POST /api/dashboards/db` as admin on the grafana container (mediastack).

## Deployment (Raspberry Pi)

Checkout: `/home/leo/documents/code/raspberrypi/google-health-grafana` (`ssh pi`).
`uv` lives at `~/.local/bin/uv` on the Pi -- not on PATH in non-interactive
`ssh pi "..."` commands.

```bash
# Fetcher + InfluxDB (Docker)
ssh pi "cd ~/documents/code/raspberrypi/google-health-grafana && git pull && docker compose up -d --build"

# Scale reader (systemd; .venv is a uv-managed editable install)
ssh pi "cd ~/documents/code/raspberrypi/google-health-grafana && git pull && ~/.local/bin/uv sync && sudo systemctl restart scale-reader"
ssh pi "systemctl status scale-reader --no-pager"

# Hevy sync (systemd oneshot + timer; manual run:)
ssh pi "sudo systemctl start hevy-sync.service && journalctl -u hevy-sync -n 3 --no-pager"
```
