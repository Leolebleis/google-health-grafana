# InfluxDB v2 -> v3 Core Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate from InfluxDB v2.7 to InfluxDB 3 Core -- swap Docker image, Python client library, config, and Grafana datasource.

**Architecture:** Drop-in replacement. Same line protocol writes, same InfluxQL queries. Client library changes from `influxdb-client` to `influxdb3-python`. No org concept in v3; bucket becomes database. Port 8086 -> 8181.

**Tech Stack:** InfluxDB 3 Core (Docker), influxdb3-python, Grafana 12.4

---

## File Map

**Modify:**
- `docker-compose.yml` -- v3 Core image + config, health-fetch env vars
- `.env` -- new vars (host/port/database, remove org/bucket/init vars)
- `.gitignore` -- influxdb3/ replaces influxdb2/
- `pyproject.toml` -- influxdb3-python replaces influxdb-client
- `requirements.txt` -- Docker build dep for fetch.py container
- `Dockerfile` -- no structural change, requirements.txt propagates
- `fetch.py` -- InfluxDBClient3, load_config, write_to_influx
- `scale/config.py` -- InfluxConfig fields (host+port+database, no org/bucket)
- `scale/config.example.yaml` -- updated fields
- `scale/main.py` -- wire new InfluxWriter params
- `scale/measurement/persistence/influx_writer.py` -- InfluxDBClient3
- `scale/measurement/persistence/influx_mapper.py` -- import path
- `tests/test_influx_writer.py` -- mock updates
- `tests/test_influx_mapper.py` -- import path

**Create:**
- `influxdb3-token` -- bearer token file (gitignored)

---

## Task 1: Docker + Environment Config

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env`
- Modify: `.gitignore`

- [ ] **Step 1: Update docker-compose.yml**

Replace full file:

```yaml
services:
  health-fetch:
    build: .
    container_name: health-fetch
    restart: unless-stopped
    volumes:
      - ./tokens:/app/tokens
    environment:
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
      - GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET}
      - TOKEN_PATH=/app/tokens/token.json
      - INFLUXDB_HOST=health-influxdb
      - INFLUXDB_PORT=8181
      - INFLUXDB_TOKEN=${INFLUXDB_TOKEN}
      - INFLUXDB_DATABASE=${INFLUXDB_DATABASE:-health}
      - DEVICE_NAME=${DEVICE_NAME:-Pixel Watch}
      - BACKFILL_DAYS=${BACKFILL_DAYS:-30}
      - INTERVAL_SECONDS=${INTERVAL_SECONDS:-300}
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
    depends_on:
      - health-influxdb

  health-influxdb:
    image: influxdb:3-core
    container_name: health-influxdb
    restart: unless-stopped
    ports:
      - "8181:8181"
    command: >
      influxdb3 serve
      --node-id=rpi-health
      --object-store=file
      --data-dir=/var/lib/influxdb3/data
      --exec-mem-pool-bytes=536870912
      --datafusion-num-threads=2
      --disable-telemetry-upload
    volumes:
      - ./influxdb3:/var/lib/influxdb3/data
```

Note: starting without auth initially. We'll add `--admin-token-file` after verifying it works.

- [ ] **Step 2: Update .env**

Replace full file:

```
GOOGLE_CLIENT_ID=<your-google-client-id>
GOOGLE_CLIENT_SECRET=<your-google-client-secret>
INFLUXDB_TOKEN=not-used-yet
INFLUXDB_DATABASE=health
```

- [ ] **Step 3: Update .gitignore**

Replace `influxdb2/` with `influxdb3/` and add `influxdb3-token`:

```
docker-compose.override.yml
.env
tokens/
influxdb/
influxdb3/
influxdb3-token
.superpowers/
scale/config.yaml
.venv/
__pycache__/
*.pyc
.pytest_cache/
.coverage
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .gitignore
git commit -m "feat: switch to InfluxDB 3 Core

Replace influxdb:2.7 with influxdb:3-core. Port 8086->8181.
512MB query memory pool, 2 DataFusion threads, no telemetry.
Remove v2 init env vars (org/bucket/password). Start without
auth for initial setup."
```

---

## Task 2: Python Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`

- [ ] **Step 1: Update pyproject.toml**

Replace `influxdb-client>=1.40.0` with `influxdb3-python>=0.14.0` in the dependencies list.

- [ ] **Step 2: Update requirements.txt** (Docker build dep for fetch.py)

Replace content:

```
influxdb3-python>=0.14.0
```

- [ ] **Step 3: Sync uv**

```bash
uv sync
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml requirements.txt uv.lock
git commit -m "chore: swap influxdb-client for influxdb3-python"
```

---

## Task 3: Scale Reader -- Config + Writer + Mapper

**Files:**
- Modify: `scale/config.py`
- Modify: `scale/config.example.yaml`
- Modify: `scale/main.py`
- Modify: `scale/measurement/persistence/influx_writer.py`
- Modify: `scale/measurement/persistence/influx_mapper.py`
- Modify: `tests/test_influx_writer.py`
- Modify: `tests/test_influx_mapper.py`

- [ ] **Step 1: Update InfluxConfig in scale/config.py**

Replace the `InfluxConfig` dataclass and the influx section of `load_config`:

```python
@dataclass(frozen=True)
class InfluxConfig:
    host: str
    port: int
    token: str
    database: str
    measurement: str
```

In `load_config`, replace the influx section:

```python
        influx=InfluxConfig(
            host=inf["host"],
            port=inf.get("port", 8181),
            token=inf["token"],
            database=inf["database"],
            measurement=inf.get("measurement", "body_composition"),
        ),
```

- [ ] **Step 2: Update scale/config.example.yaml**

Replace the influxdb section:

```yaml
influxdb:
  host: "localhost"
  port: 8181
  token: "your-influxdb-admin-token"
  database: "health"
  measurement: "body_composition"
```

- [ ] **Step 3: Update influx_mapper.py import**

Replace:
```python
from influxdb_client import Point
```
With:
```python
from influxdb3 import Point
```

The `Point` builder API is identical between v2 and v3 clients.

- [ ] **Step 4: Update influx_writer.py**

Replace the full file:

```python
import logging

from influxdb3 import InfluxDBClient3, Point

from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.model.measurement import Measurement
from scale.measurement.persistence.influx_mapper import to_influx_point

log = logging.getLogger(__name__)


class InfluxWriter:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        database: str,
        user: str,
        measurement_name: str = "body_composition",
    ) -> None:
        self._client = InfluxDBClient3(host=host, port=port, token=token, database=database)
        self._user = user
        self._measurement_name = measurement_name

    def persist(self, measurement: Measurement, body_composition: BodyComposition) -> None:
        point = to_influx_point(
            body_composition,
            measurement.timestamp,
            user=self._user,
            measurement_name=self._measurement_name,
        )
        self._client.write(record=point)
        log.info("Wrote body composition to InfluxDB")

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 5: Update scale/main.py wiring**

Replace the `InfluxWriter(...)` constructor call:

```python
    writer = InfluxWriter(
        host=cfg.influx.host,
        port=cfg.influx.port,
        token=cfg.influx.token,
        database=cfg.influx.database,
        user=cfg.user.name,
        measurement_name=cfg.influx.measurement,
    )
```

- [ ] **Step 6: Update tests/test_influx_mapper.py**

Replace the import:
```python
from influxdb_client import Point
```
With... nothing. The test doesn't import Point directly -- it calls `to_influx_point` which returns a Point. But verify the `to_line_protocol()` method exists on the v3 Point class. If the test calls `.to_line_protocol()`, check that the v3 Point supports it. If not, replace with `str(point)` or the v3 equivalent.

Actually, the v3 `influxdb3.Point` has `to_line_protocol()` -- same API. No test changes needed beyond verifying it passes.

- [ ] **Step 7: Update tests/test_influx_writer.py**

Replace mock target and constructor args:

```python
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.model.measurement import Measurement
from scale.measurement.persistence.influx_writer import InfluxWriter


def _make_body_composition() -> BodyComposition:
    return BodyComposition(
        weight_kg=70.0,
        bmi=22.9,
        body_fat_pct=18.0,
        water_pct=60.0,
        muscle_mass_kg=35.0,
        bone_mass_kg=3.0,
        protein_pct=16.0,
        visceral_fat=8.0,
        bmr_kcal=1700.0,
        metabolic_age=30,
        ideal_weight_kg=68.0,
        body_type=5,
        heart_rate=72,
        impedance=490.0,
    )


def _make_measurement() -> Measurement:
    return Measurement(
        weight_kg=70.0,
        impedance=490.0,
        heart_rate=72,
        timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


@patch("scale.measurement.persistence.influx_writer.InfluxDBClient3")
def test_persist_calls_write(mock_client_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    writer = InfluxWriter(
        host="localhost",
        port=8181,
        token="token",
        database="health",
        user="alice",
        measurement_name="body_composition",
    )

    writer.persist(_make_measurement(), _make_body_composition())

    mock_client.write.assert_called_once()
    call_kwargs = mock_client.write.call_args
    assert call_kwargs.kwargs["record"] is not None


@patch("scale.measurement.persistence.influx_writer.InfluxDBClient3")
def test_close_calls_client_close(mock_client_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    writer = InfluxWriter(
        host="localhost",
        port=8181,
        token="token",
        database="health",
        user="alice",
    )
    writer.close()

    mock_client.close.assert_called_once()


@patch("scale.measurement.persistence.influx_writer.InfluxDBClient3")
def test_influx_client_constructed_with_correct_args(mock_client_cls: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    InfluxWriter(
        host="influx-host",
        port=8181,
        token="tok",
        database="mydb",
        user="bob",
    )

    mock_client_cls.assert_called_once_with(host="influx-host", port=8181, token="tok", database="mydb")
```

- [ ] **Step 8: Run tests**

```bash
uv run pytest tests/ -v
```

Expected: all 37 tests PASS.

- [ ] **Step 9: Run lint**

```bash
uv run ruff check scale/ tests/ fetch.py
```

Expected: 0 errors.

- [ ] **Step 10: Commit**

```bash
git add scale/ tests/
git commit -m "feat(scale): migrate to influxdb3-python client

Replace influxdb-client with influxdb3-python in config, writer,
mapper, and tests. Remove org/bucket concepts (v3 uses database).
Host+port replaces URL."
```

---

## Task 4: Migrate fetch.py

**Files:**
- Modify: `fetch.py`

- [ ] **Step 1: Update imports**

Replace:
```python
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
```
With:
```python
from influxdb3 import InfluxDBClient3, Point
```

- [ ] **Step 2: Update load_config**

Replace the influx config keys:

```python
        "influx_host": os.environ.get("INFLUXDB_HOST", "localhost"),
        "influx_port": int(os.environ.get("INFLUXDB_PORT", "8181")),
        "influx_token": os.environ.get("INFLUXDB_TOKEN", ""),
        "influx_database": os.environ.get("INFLUXDB_DATABASE", "health"),
```

Remove: `influx_url`, `influx_org`, `influx_bucket`.

- [ ] **Step 3: Update write_to_influx**

Replace the full function:

```python
def write_to_influx(cfg: dict, points: list) -> None:
    if not points:
        return
    client = InfluxDBClient3(
        host=cfg["influx_host"],
        port=cfg["influx_port"],
        token=cfg["influx_token"],
        database=cfg["influx_database"],
    )

    influx_points = []
    for p in points:
        fields = {}
        for k, v in p["fields"].items():
            if v is None:
                continue
            fields[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
        if not fields:
            continue
        point = Point(p["measurement"]).tag("Device", cfg["device_name"]).time(p["time"])
        for k, v in fields.items():
            point = point.field(k, v)
        influx_points.append(point)

    if influx_points:
        client.write(record=influx_points)
        log.info("Wrote %s points to InfluxDB", len(influx_points))

    client.close()
```

- [ ] **Step 4: Run lint**

```bash
uv run ruff check fetch.py
```

- [ ] **Step 5: Commit**

```bash
git add fetch.py
git commit -m "feat: migrate fetch.py to influxdb3-python

Replace InfluxDBClient with InfluxDBClient3. Remove org/bucket,
use host+port+database. Synchronous write via client.write()."
```

---

## Task 5: Deploy + Verify

**Manual steps on the Pi.**

- [ ] **Step 1: Stop current stack**

```bash
docker compose down
sudo systemctl stop scale-reader
```

- [ ] **Step 2: Remove old v2 data**

```bash
rm -rf influxdb2/
```

- [ ] **Step 3: Update scale config.yaml**

Replace the influxdb section:

```yaml
influxdb:
  host: "localhost"
  port: 8181
  token: ""
  database: "health"
  measurement: "body_composition"
```

Token is empty for now (no auth on initial startup).

- [ ] **Step 4: Update .env with real credentials**

```
GOOGLE_CLIENT_ID=<real-id>
GOOGLE_CLIENT_SECRET=<real-secret>
INFLUXDB_TOKEN=
INFLUXDB_DATABASE=health
```

- [ ] **Step 5: Sync uv and rebuild**

```bash
uv sync
docker compose build health-fetch
```

- [ ] **Step 6: Start InfluxDB 3 Core**

```bash
docker compose up -d health-influxdb
sleep 5
curl -s http://localhost:8181/health
```

Expected: healthy response.

- [ ] **Step 7: Start health-fetch and verify backfill**

Temporarily set `BACKFILL_DAYS=90` in `.env`, then:

```bash
docker compose up -d health-fetch
docker compose logs -f health-fetch
```

Expected: syncs ~7000 points to InfluxDB.

- [ ] **Step 8: Verify data via InfluxQL**

```bash
curl -s 'http://localhost:8181/query?db=health' \
  --data-urlencode 'q=SELECT last("value") FROM "weight"'
```

Expected: returns weight data.

- [ ] **Step 9: Restart scale reader**

```bash
sudo systemctl restart scale-reader
sudo systemctl status scale-reader
```

- [ ] **Step 10: Update Grafana datasource**

Via API:

```bash
curl -s -X PUT http://<grafana-ip>:3000/api/datasources/2 \
  -u admin:admin \
  -H 'Content-Type: application/json' \
  -d '{
    "id": 2,
    "uid": "P9A8567EC67EE4A5C",
    "name": "InfluxDB Health",
    "type": "influxdb",
    "access": "proxy",
    "url": "http://health-influxdb:8181",
    "database": "health",
    "jsonData": {
      "httpMode": "POST"
    }
  }'
```

No auth header needed since we started without auth.

- [ ] **Step 11: Verify dashboards**

Check both dashboards show data. Step on scale to verify end-to-end.
