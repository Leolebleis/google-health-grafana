# Xiaomi S400 Scale Reader -- Design Spec

## Overview

Headless Python daemon on Raspberry Pi 4 that reads Xiaomi Body Composition Scale S400 BLE data, decrypts MiBeacon v5 advertisements, calculates 14 body composition metrics, and writes to InfluxDB v2 for Grafana visualization. Includes upgrading the existing InfluxDB v1 instance to v2.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Where it lives | Same repo (`google-health-grafana`), new `scale/` directory | All health data in one place, shared InfluxDB |
| InfluxDB version | Upgrade v1.11 -> v2.7 | Building on deprecated stack is wrong; new service starts on v2 |
| Runtime | Host systemd service (not Docker) | BLE access is simpler outside Docker; writes to Dockerized InfluxDB over localhost |
| BLE approach | Port openScale's `S400Decryptor.kt` to Python | Self-contained, test vectors available, no HA dependency |
| Users | Single user (Leo) | Config file with profile; no multi-user routing needed |
| Dashboard | Separate Grafana dashboard | Dedicated body composition view, own time ranges |

## Project Structure

```
google-health-grafana/
  fetch.py                          # existing Google Health fetcher (migrated to v2 client)
  docker-compose.yml                # InfluxDB v2 + health-fetch
  scale/
    main.py                         # entry point, wiring, main loop
    config.py                       # loads config.yaml, typed config model
    config.yaml                     # MAC, bindkey, user profile, InfluxDB connection
    requirements.txt                # bleak, pycryptodome, influxdb-client, pyyaml
    scale-reader.service            # systemd unit file
    measurement/
      model/
        measurement.py              # domain entity: weight, impedance, heart_rate, ts
        body_composition.py         # domain entity: fat%, muscle%, bone%, water%, etc.
        user_profile.py             # domain entity: age, sex, height
      service.py                    # orchestrates: scan -> decrypt -> calculate -> persist
      calculator.py                 # body composition formulas (ported from openScale)
      dao.py                        # abstract persistence protocol
      scanner_facade.py             # abstract BLE scanner protocol
      scanner/
        ble_scanner.py              # concrete: bleak + MiBeacon v5 AES-CCM decryption
      persistence/
        influx_writer.py            # concrete: implements DAO, writes to InfluxDB v2
        influx_mapper.py            # domain entity -> InfluxDB point
  scale-dashboard.json              # Grafana dashboard for body composition
```

### Layer Boundaries (Clean Architecture)

- **Domain** (`model/`, `service.py`, `calculator.py`, `dao.py`, `scanner_facade.py`): knows nothing about BLE or InfluxDB. Pure entities, business logic, and abstract protocols.
- **Infrastructure** (`scanner/`, `persistence/`): implements abstract protocols. `ble_scanner.py` implements `ScannerFacade`, `influx_writer.py` implements `MeasurementDAO`.
- `calculator.py` takes `Measurement` + `UserProfile`, returns `BodyComposition`. Pure math, zero dependencies.
- Infrastructure mappers (`influx_mapper.py`) are internal to the persistence layer. The service never sees InfluxDB concepts.

## Data Flow

```
Step on scale
    |
    v
BLE Advertisement (encrypted MiBeacon v5, UUID 0xFE95)
    |
    v
ble_scanner.py (bleak passive scan)
    - Detects S400 by MAC address
    - AES-CCM decrypt with bindkey (pycryptodome)
    - Parses weight, impedance, heart_rate from decrypted payload
    - Returns Measurement domain entity
    |
    v
service.py (orchestrator)
    - Receives Measurement from scanner
    - Calls calculator.py with Measurement + UserProfile
    - Receives BodyComposition entity (14 metrics)
    - Deduplication: ignores measurements within 30s of the last one
    - Calls DAO.persist(measurement, body_composition)
    - Logs success
    |
    v
influx_writer.py (implements DAO)
    - influx_mapper.py maps entities to InfluxDB line protocol
    - Writes to bucket "health", measurement "body_composition"
    |
    v
Grafana reads from InfluxDB v2
```

## BLE Protocol: MiBeacon v5 Decryption

The S400 broadcasts encrypted BLE advertisements using MiBeacon v5 on service UUID `0xFE95`.

**Decryption (ported from openScale `S400Decryptor.kt`):**
- Nonce: reversed MAC (6 bytes) + advertisement data bytes (extracted per protocol)
- Ciphertext: encrypted payload + 4-byte MIC tag
- Algorithm: AES-CCM with bindkey (16 bytes), `mac_len=4`, AAD = `b'\x11'`
- Library: `pycryptodome` (`Crypto.Cipher.AES`, `MODE_CCM`)

**Decrypted payload parsing:**
- Weight, heart rate, impedance extracted via bit masks from a packed 32-bit integer
- Object type `0x6E16` identifies S400-specific data

**Validation:** openScale provides test vectors in `S400DecryptorTest.kt`:
- MAC: `84:46:93:64:A5:E6`
- Bindkey: `58305740b64e4b425e518aa1f4e51339`
- Known input -> expected output pairs for unit tests

## Body Composition Metrics

14 metrics calculated from weight + impedance + user profile. Formulas ported from openScale's `MiScaleLib.kt`.

| Metric | Unit | Formula source |
|--------|------|---------------|
| weight | kg | Raw from scale |
| bmi | - | weight / (height_m)^2 |
| body_fat_pct | % | Deurenberg formula (BMI + age + sex) |
| water_pct | % | Derived from fat-free mass |
| muscle_mass | kg | Janssen model (impedance + height + sex) |
| bone_mass | kg | Estimated from fat-free mass |
| protein_pct | % | Derived from muscle + water |
| visceral_fat | index | Waist estimate from BMI + age |
| bmr | kcal/day | Mifflin-St Jeor equation |
| metabolic_age | years | BMR compared to age-normed tables |
| ideal_weight | kg | BMI 22 target for height |
| body_type | 1-9 | Fat% vs muscle% grid |
| heart_rate | bpm | Raw from scale hardware |
| impedance | ohm | Raw bioimpedance value |

The S400 supports dual-frequency impedance (50kHz + 250kHz). Starting with openScale's single-frequency formulas; can upgrade to bodymiscale's dual-frequency clinical formulas later.

## User Profile

```yaml
user:
  name: leo
  sex: male
  height_cm: 178
  birth_date: "1997-04-08"
```

Age is calculated dynamically at measurement time from `birth_date`.

## InfluxDB v1 -> v2 Migration

**Approach:** Fresh v2 instance + re-backfill from Google Health API. No v1-to-v2 data migration tool needed.

**Docker changes:**
- Image: `influxdb:1.11` -> `influxdb:2.7`
- Auth: username/password -> org/token/bucket
- Volume: `./influxdb` -> `./influxdb2` (fresh volume)
- Init: `DOCKER_INFLUXDB_INIT_MODE=setup` with org `home`, bucket `health`

**fetch.py changes:**
- Python client: `influxdb` -> `influxdb-client`
- Write API: `client.write_points()` -> batch write API with `write_api.write()`
- Connection: host/port/user/pass -> URL/token/org
- Bump `BACKFILL_DAYS` for initial re-fill

**Grafana:** Update datasource to InfluxDB v2. InfluxQL queries still work via v2's compatibility endpoint; migrate to Flux over time.

## InfluxDB Schema

```
measurement: body_composition
tags:
  user: leo
fields:
  weight: float (kg)
  bmi: float
  body_fat_pct: float (%)
  water_pct: float (%)
  muscle_mass: float (kg)
  bone_mass: float (kg)
  protein_pct: float (%)
  visceral_fat: float (index)
  bmr: float (kcal/day)
  metabolic_age: float (years)
  ideal_weight: float (kg)
  body_type: integer (1-9)
  heart_rate: integer (bpm)
  impedance: float (ohm)
timestamp: measurement time from scale
```

Bucket: `health` (shared with Google Health data from fetch.py).

## Configuration

```yaml
scale:
  mac: "XX:XX:XX:XX:XX:XX"
  bindkey: "abcdef1234..."
  scan_timeout: 300

user:
  name: leo
  sex: male
  height_cm: 178
  birth_date: "1997-04-08"

influxdb:
  url: "http://localhost:8086"
  token: "<admin-token>"
  org: "home"
  bucket: "health"
  measurement: "body_composition"

dedup_window_seconds: 30
log_level: INFO
```

`config.yaml` is gitignored (contains bindkey and InfluxDB token). A `config.example.yaml` is committed with placeholder values. `.gitignore` must be updated to include `scale/config.yaml`.

## Systemd Service

```ini
[Unit]
Description=Xiaomi S400 Scale Reader
After=bluetooth.target

[Service]
Type=simple
User=leo
WorkingDirectory=/home/leo/documents/code/raspberrypi/google-health-grafana/scale
ExecStart=/home/leo/documents/code/raspberrypi/google-health-grafana/scale/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Grafana Dashboard

Separate `scale-dashboard.json` with panels:
- Weight trend (line chart, 90-day default)
- Body fat % trend
- Muscle mass trend
- BMI gauge
- Heart rate at weigh-in
- Latest reading table (all 14 metrics)

## One-Time Setup Prerequisites

1. Enable Bluetooth: `rfkill unblock bluetooth`, power on adapter
2. Install Xiaomi Home app on phone, pair scale, complete one measurement
3. Run Xiaomi-cloud-tokens-extractor to get MAC + bindkey
4. Delete app -- never needed again
5. Add MAC + bindkey to `config.yaml`

## Dependencies

```
bleak>=0.21.0
pycryptodome>=3.20.0
influxdb-client>=1.40.0
pyyaml>=6.0
```

Python 3.11+ (system Python on Pi OS Trixie).
