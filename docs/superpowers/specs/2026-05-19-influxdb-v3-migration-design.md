# InfluxDB v2 -> v3 Core Migration -- Design Spec

## Overview

Migrate from InfluxDB v2.7 to InfluxDB 3 Core on Raspberry Pi 4 (8GB). Swap Docker image, update both Python services to use `influxdb3-python`, update Grafana datasource, re-backfill data.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| v3 variant | 3 Core (not Enterprise) | Free, MIT/Apache 2.0, single-node is all we need |
| Memory pool | 512MB | Low-volume health metrics; default 8GB would OOM the Pi |
| Query threads | 2 | Leave 2 cores for OS + other containers |
| Auth | Token via `--admin-token-file` | Deterministic startup, no manual token creation |
| Query language | Keep InfluxQL | Supported in v3, avoids rewriting all dashboard queries |
| Data migration | Re-backfill from Google Health API + fresh scale data | Only ~30 days of API data, 2 scale readings -- not worth a formal migration |

## Docker Changes

Replace `health-influxdb` service in `docker-compose.yml`:

```yaml
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
    --admin-token-file=/var/lib/influxdb3/auth-token
  volumes:
    - ./influxdb3:/var/lib/influxdb3/data
    - ./influxdb3-token:/var/lib/influxdb3/auth-token:ro
```

`influxdb3-token` is a single file containing the bearer token, created once via `openssl rand -hex 32 > influxdb3-token`. Mounted read-only. Gitignored.

### Port change

8086 -> 8181. Affects:
- `docker-compose.yml` port mapping
- `health-fetch` environment (`INFLUXDB_URL`)
- `scale/config.yaml` (`influxdb.url`)
- Grafana datasource URL

### Auth change

v2 used org/token/bucket. v3 uses bearer token + database (no org concept).

## Python Client Changes

### Dependency

`influxdb-client` -> `influxdb3-python` in `pyproject.toml`:

```toml
dependencies = [
    "bleak>=0.21.0",
    "pycryptodome>=3.20.0",
    "influxdb3-python>=0.14.0",
    "pyyaml>=6.0",
]
```

### fetch.py

```python
# v2
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
client = InfluxDBClient(url=url, token=token, org=org)
write_api = client.write_api(write_options=SYNCHRONOUS)
write_api.write(bucket=bucket, record=points)
client.close()

# v3
from influxdb3 import InfluxDBClient3, Point
client = InfluxDBClient3(host=host, port=port, token=token, database=database)
client.write(record=points)
client.close()
```

Config changes: remove `INFLUXDB_ORG`, rename `INFLUXDB_BUCKET` -> `INFLUXDB_DATABASE`, split URL into host+port.

### scale/measurement/persistence/influx_writer.py

Same pattern -- swap `InfluxDBClient` for `InfluxDBClient3`, remove org, bucket -> database.

### scale/measurement/persistence/influx_mapper.py

`influxdb3-python` has its own `Point` class with the same builder API. Import path changes from `influxdb_client` to `influxdb3`.

## Config Changes

### .env

```
INFLUXDB_TOKEN=<token-from-file>
INFLUXDB_HOST=health-influxdb
INFLUXDB_PORT=8181
INFLUXDB_DATABASE=health
```

Remove: `INFLUXDB_ORG`, `INFLUXDB_BUCKET`, `INFLUXDB_INIT_PASSWORD`, `INFLUXDB_INIT_USERNAME`.

### docker-compose.yml health-fetch environment

```yaml
environment:
  - INFLUXDB_HOST=health-influxdb
  - INFLUXDB_PORT=8181
  - INFLUXDB_TOKEN=${INFLUXDB_TOKEN}
  - INFLUXDB_DATABASE=${INFLUXDB_DATABASE:-health}
```

### scale/config.yaml

```yaml
influxdb:
  host: "localhost"
  port: 8181
  token: "<token>"
  database: "health"
  measurement: "body_composition"
```

Remove: `org`, `bucket`. Rename `url` -> `host` + `port`.

### scale/config.example.yaml

Same structure with placeholder values.

## Grafana Datasource

Update the existing InfluxDB datasource:
- Product: "InfluxDB 3.x" (or keep as InfluxDB with InfluxQL mode)
- URL: `http://health-influxdb:8181`
- Database: `health`
- Auth: Bearer token in custom header
- Query language: InfluxQL (no dashboard query changes needed)

## .gitignore

Add:
- `influxdb3/`
- `influxdb3-token`

Remove:
- `influxdb2/`

## Data

- Delete `influxdb2/` volume (v2 data)
- Bump `BACKFILL_DAYS=90` temporarily in `.env` for initial re-fill
- Scale data starts fresh (2 readings lost -- acceptable)

## Files Modified

- `docker-compose.yml` -- new influxdb image + config, health-fetch env vars
- `.env` -- new vars (host/port/database replacing org/bucket)
- `.gitignore` -- influxdb3/ replaces influxdb2/
- `pyproject.toml` -- influxdb3-python replaces influxdb-client
- `fetch.py` -- InfluxDBClient3, load_config, write_to_influx
- `scale/config.py` -- InfluxConfig fields (host+port+database, no org)
- `scale/config.example.yaml` -- updated fields
- `scale/measurement/persistence/influx_writer.py` -- InfluxDBClient3
- `scale/measurement/persistence/influx_mapper.py` -- import path
- `tests/test_influx_writer.py` -- mock updates
- `tests/test_influx_mapper.py` -- import path
- Grafana datasource (API call, not a file)
