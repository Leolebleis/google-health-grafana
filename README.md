# google-health-grafana

Personal health-data pipeline on a Raspberry Pi: weight, workouts, food, and
watch metrics from four sources, unified in InfluxDB 3 Core and visualized in
Grafana — with the same data queryable by LLM agents (Hevy MCP + InfluxDB SQL).

```
Pixel Watch ──► Google Health API ──► fetch.py (Docker, 5 min) ─────────┐
MyFitnessPal ─► (MFP→Fitbit bridge) ──┘                                 │
                                                                        ▼
Xiaomi S400 ──► Xiaomi Home cloud ──► SmartScaleConnect (hourly) ──► InfluxDB 3
     │                                                                  ▲    │
     └────────► BLE advertisements ──► scale-reader (systemd) ──────────┘    ▼
                                                                          Grafana
Hevy app ─────► webhook ──► Tailscale Funnel ──► hevy-webhook ──► hevy.sync ─┘
```

## Components

| Component | Code | Runs as | Writes |
|-----------|------|---------|--------|
| Google Health fetcher | `fetch.py` (single file) | Docker `health-fetch`, polls every 5 min | fitbit-grafana-compatible measurements (`weight`, `HeartRate_Intraday`, `Sleep Summary`, `calories`, `Nutrition`, ...) |
| Xiaomi S400 BLE reader | `scale/` package | systemd `scale-reader` (host Bluetooth) | `body_composition` |
| Xiaomi cloud scale sync | `scale/cloud_sync.py` | systemd `scale-sync.timer` (hourly), runs [SmartScaleConnect](https://github.com/AlexxIT/SmartScaleConnect) via docker | `body_composition` |
| Hevy workout sync | `hevy/` package | webhook-driven: `hevy-webhook` receiver behind Tailscale Funnel triggers `hevy.sync` per saved workout | `workout`, `workout_set` |
| InfluxDB 3 Core | `docker-compose.yml` | Docker `health-influxdb`, port 8181, no auth | — |

Grafana itself runs in a separate stack; `dashboard.json` (Health),
`scale-dashboard.json` (Body Composition), and `training-dashboard.json`
(Training) are exports deployed via `POST /api/dashboards/db`.

## Highlights

- **Energy balance**: calories in (MyFitnessPal via Google Health's
  `nutrition-log` data type) vs. burned (baseline + active split), with a net
  deficit/surplus line.
- **Training targets**: weekly side-delt/back set counts, per-exercise
  progression, and instant sync — a saved Hevy workout appears in Grafana
  within seconds via webhook.
- **Self-healing syncs**: every collector watermarks on `MAX(time)` and
  catches up on whatever it missed.

## Development

```bash
uv sync                                  # Python 3.13, deps + dev group
uv run pytest --cov -v                   # 80% coverage gate
uv run ruff check scale/ hevy/ tests/
uv run ruff format --check scale/ hevy/ tests/
uv run ty check scale/ hevy/
```

CI (GitHub Actions) runs exactly these. `fetch.py` is deliberately a
standalone script outside lint/type/test scope.

## Deployment

Runs on a Raspberry Pi 4. Docker services via `docker compose up -d --build`;
systemd units live next to their code (`scale/*.service`, `scale/*.timer`,
`hevy/*.service`) and are copied to `/etc/systemd/system/`.

Secrets are gitignored and live only on the Pi:

| File | Contents |
|------|----------|
| `.env` | Google OAuth client + InfluxDB env for the compose |
| `tokens/` | Google OAuth refresh token |
| `scale/config.yaml` | scale MAC + BLE bindkey, user profile (see `scale/config.example.yaml`) |
| `scale-sync/scaleconnect.yaml` + `scaleconnect.json` | Xiaomi account + cached passToken (see the example file) |
| `hevy-sync/env` | Hevy Pro API key + webhook auth token (see `env.example`) |

The Hevy webhook is exposed publicly via **Tailscale Funnel on port 8443**
(`https://<host>.<tailnet>.ts.net:8443/hevy-webhook`); port 443 must stay with
nginx — tailscaled would otherwise intercept it for tailnet clients.

## Known constraints

- The Hevy API has no `DELETE`; removing a workout must happen in the app and
  won't propagate to InfluxDB (drop the `workout`/`workout_set` tables to
  resync from scratch).
- MyFitnessPal meals reach Google Health over the legacy MFP→Fitbit bridge,
  which Google turns down in **September 2026** — if meals stop arriving then,
  the bridge died before MFP migrated.
- InfluxDB 3 Core has no compaction: collectors only rewrite a short recent
  window (`INCREMENTAL_WINDOW_DAYS`), and the server runs with a raised
  `--query-file-limit`.
