# Xiaomi S400 Scale Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a headless Python daemon that reads Xiaomi S400 BLE scale data, calculates body composition, and writes to InfluxDB v2 for Grafana -- including upgrading the existing InfluxDB v1 stack.

**Architecture:** Clean Architecture with domain entities/protocols at the core, infrastructure (BLE scanner, InfluxDB writer) implementing abstract protocols. Host systemd service for BLE access, writing to Dockerized InfluxDB v2.

**Tech Stack:** Python 3.11+, bleak (BLE), pycryptodome (AES-CCM), influxdb-client (v2), pyyaml, pytest

---

## File Map

**Modify:**
- `docker-compose.yml` -- InfluxDB v1 -> v2
- `.env` -- new v2 auth vars
- `.gitignore` -- add scale/config.yaml, influxdb2/
- `requirements.txt` -- influxdb -> influxdb-client
- `fetch.py` -- migrate to influxdb-client v2 API
- `Dockerfile` -- no changes needed (requirements.txt change propagates)

**Create:**
- `scale/measurement/model/measurement.py` -- domain entity
- `scale/measurement/model/body_composition.py` -- domain entity
- `scale/measurement/model/user_profile.py` -- domain entity
- `scale/measurement/calculator.py` -- body composition formulas
- `scale/measurement/dao.py` -- abstract persistence protocol
- `scale/measurement/scanner_facade.py` -- abstract scanner protocol
- `scale/measurement/service.py` -- orchestrator
- `scale/measurement/scanner/ble_scanner.py` -- BLE + S400 decryption
- `scale/measurement/scanner/s400_decrypt.py` -- S400 AES-CCM decryptor
- `scale/measurement/persistence/influx_writer.py` -- InfluxDB v2 writer
- `scale/measurement/persistence/influx_mapper.py` -- entity -> InfluxDB point
- `scale/config.py` -- config loader
- `scale/config.example.yaml` -- committed template
- `scale/main.py` -- entry point
- `scale/requirements.txt` -- Python dependencies
- `scale/scale-reader.service` -- systemd unit
- `scale/measurement/__init__.py` -- package init
- `scale/measurement/model/__init__.py`
- `scale/measurement/scanner/__init__.py`
- `scale/measurement/persistence/__init__.py`
- `scale-dashboard.json` -- Grafana dashboard
- `tests/test_s400_decrypt.py` -- decryptor tests
- `tests/test_calculator.py` -- body composition tests
- `tests/test_influx_mapper.py` -- mapper tests
- `tests/test_service.py` -- service tests

---

## Task 1: InfluxDB v2 Docker + Environment

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env`
- Modify: `.gitignore`

- [ ] **Step 1: Update docker-compose.yml**

Replace the full file content:

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
      - INFLUXDB_URL=http://health-influxdb:8086
      - INFLUXDB_TOKEN=${INFLUXDB_TOKEN}
      - INFLUXDB_ORG=${INFLUXDB_ORG:-home}
      - INFLUXDB_BUCKET=${INFLUXDB_BUCKET:-health}
      - DEVICE_NAME=${DEVICE_NAME:-Pixel Watch}
      - BACKFILL_DAYS=${BACKFILL_DAYS:-30}
      - INTERVAL_SECONDS=${INTERVAL_SECONDS:-300}
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
    depends_on:
      - health-influxdb

  health-influxdb:
    image: influxdb:2.7
    container_name: health-influxdb
    restart: unless-stopped
    ports:
      - "8086:8086"
    environment:
      - DOCKER_INFLUXDB_INIT_MODE=setup
      - DOCKER_INFLUXDB_INIT_USERNAME=${INFLUXDB_INIT_USERNAME:-leo}
      - DOCKER_INFLUXDB_INIT_PASSWORD=${INFLUXDB_INIT_PASSWORD}
      - DOCKER_INFLUXDB_INIT_ORG=${INFLUXDB_ORG:-home}
      - DOCKER_INFLUXDB_INIT_BUCKET=${INFLUXDB_BUCKET:-health}
      - DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=${INFLUXDB_TOKEN}
    volumes:
      - ./influxdb2:/var/lib/influxdb2
```

- [ ] **Step 2: Update .env**

Replace the full file content:

```
GOOGLE_CLIENT_ID=<your-google-client-id>
GOOGLE_CLIENT_SECRET=<your-google-client-secret>
INFLUXDB_TOKEN=<your-influxdb-token>
INFLUXDB_INIT_PASSWORD=<your-influxdb-password>
INFLUXDB_ORG=home
INFLUXDB_BUCKET=health
```

- [ ] **Step 3: Update .gitignore**

Replace the full file content:

```
docker-compose.override.yml
.env
tokens/
influxdb/
influxdb2/
.superpowers/
scale/config.yaml
scale/.venv/
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .gitignore
git commit -m "feat: upgrade InfluxDB from v1.11 to v2.7

Switch to token-based auth, org/bucket model. Fresh v2 volume.
Port 8086 exposed for host-side scale reader access."
```

Note: `.env` is gitignored so it won't be committed.

---

## Task 2: Migrate fetch.py to InfluxDB v2

**Files:**
- Modify: `requirements.txt`
- Modify: `fetch.py`

- [ ] **Step 1: Update requirements.txt**

Replace content:

```
influxdb-client==1.46.0
```

- [ ] **Step 2: Update fetch.py imports and config**

Replace lines 20-45 (the import and `load_config` function):

```python
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

HEALTH_API = "https://health.googleapis.com/v4"
TOKEN_URL = "https://oauth2.googleapis.com/token"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def load_config():
    return {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "token_path": Path(os.environ.get("TOKEN_PATH", "/app/tokens/token.json")),
        "influx_url": os.environ.get("INFLUXDB_URL", "http://localhost:8086"),
        "influx_token": os.environ.get("INFLUXDB_TOKEN", ""),
        "influx_org": os.environ.get("INFLUXDB_ORG", "home"),
        "influx_bucket": os.environ.get("INFLUXDB_BUCKET", "health"),
        "device_name": os.environ.get("DEVICE_NAME", "Pixel Watch"),
        "backfill_days": int(os.environ.get("BACKFILL_DAYS", "7")),
        "interval_seconds": int(os.environ.get("INTERVAL_SECONDS", "300")),
    }
```

- [ ] **Step 3: Replace write_to_influx function**

Replace the entire `write_to_influx` function (lines 418-454):

```python
def write_to_influx(cfg: dict, points: list):
    if not points:
        return
    client = InfluxDBClient(
        url=cfg["influx_url"],
        token=cfg["influx_token"],
        org=cfg["influx_org"],
    )
    write_api = client.write_api(write_options=SYNCHRONOUS)

    influx_points = []
    for p in points:
        fields = {}
        for k, v in p["fields"].items():
            if v is None:
                continue
            fields[k] = (
                float(v)
                if isinstance(v, (int, float)) and not isinstance(v, bool)
                else v
            )
        if not fields:
            continue
        point = Point(p["measurement"]).tag("Device", cfg["device_name"]).time(p["time"])
        for k, v in fields.items():
            point = point.field(k, v)
        influx_points.append(point)

    if influx_points:
        write_api.write(bucket=cfg["influx_bucket"], record=influx_points)
        log.info(f"Wrote {len(influx_points)} points to InfluxDB")

    client.close()
```

- [ ] **Step 4: Remove old influxdb import**

Remove line `from influxdb import InfluxDBClient` (line 20 in original).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt fetch.py
git commit -m "feat: migrate fetch.py to InfluxDB v2 client

Replace influxdb==5.3.2 with influxdb-client==1.46.0.
Switch from host/port/user/pass to URL/token/org/bucket.
Use Point objects and synchronous write API."
```

---

## Task 3: Domain Entities

**Files:**
- Create: `scale/measurement/__init__.py`
- Create: `scale/measurement/model/__init__.py`
- Create: `scale/measurement/model/measurement.py`
- Create: `scale/measurement/model/body_composition.py`
- Create: `scale/measurement/model/user_profile.py`

- [ ] **Step 1: Create package init files**

Create `scale/measurement/__init__.py` (empty file).
Create `scale/measurement/model/__init__.py` (empty file).

- [ ] **Step 2: Create Measurement entity**

Create `scale/measurement/model/measurement.py`:

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Measurement:
    weight_kg: float
    impedance: float | None
    heart_rate: int | None
    timestamp: datetime
```

- [ ] **Step 3: Create UserProfile entity**

Create `scale/measurement/model/user_profile.py`:

```python
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class UserProfile:
    name: str
    sex: str
    height_cm: int
    birth_date: date

    def age_at(self, when: date) -> int:
        years = when.year - self.birth_date.year
        if (when.month, when.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years
```

- [ ] **Step 4: Create BodyComposition entity**

Create `scale/measurement/model/body_composition.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class BodyComposition:
    weight_kg: float
    bmi: float
    body_fat_pct: float
    water_pct: float
    muscle_mass_kg: float
    bone_mass_kg: float
    protein_pct: float
    visceral_fat: float
    bmr_kcal: float
    metabolic_age: int
    ideal_weight_kg: float
    body_type: int
    heart_rate: int | None
    impedance: float | None
```

- [ ] **Step 5: Commit**

```bash
git add scale/measurement/__init__.py scale/measurement/model/
git commit -m "feat(scale): add domain entities

Measurement (raw scale data), BodyComposition (14 calculated metrics),
UserProfile (age/sex/height for formulas)."
```

---

## Task 4: S400 Decryptor

**Files:**
- Create: `scale/measurement/scanner/__init__.py`
- Create: `scale/measurement/scanner/s400_decrypt.py`
- Create: `tests/test_s400_decrypt.py`

- [ ] **Step 1: Write failing tests (from openScale test vectors)**

Create `tests/test_s400_decrypt.py`:

```python
import pytest
from scale.measurement.scanner.s400_decrypt import s400_decrypt, S400RawData


MAC = "84:46:93:64:A5:E6"
BIND_KEY = "58305740b64e4b425e518aa1f4e51339"


def test_decrypt_24_byte_payload():
    data = bytes.fromhex("4859d53b2d3314943c58b133638c7457a4000000c3e670dc")
    result = s400_decrypt(data, MAC, BIND_KEY)
    assert result is not None
    assert abs(result.weight_kg - 74.2) < 0.1


def test_decrypt_26_byte_payload():
    data = bytes.fromhex("95FE4859D53B3BDE6BC8D05B51C0CDFD9021C9000000925C5039")
    result = s400_decrypt(data, MAC, BIND_KEY)
    assert result is not None
    assert abs(result.weight_kg - 73.2) < 0.1


def test_decrypt_26_byte_payload_variant():
    data = bytes([149, 254, 72, 89, 213, 59, 77, 111, 53, 156, 229, 111,
                  31, 126, 126, 10, 221, 220, 38, 0, 0, 0, 12, 19, 211, 196])
    result = s400_decrypt(data, MAC, BIND_KEY)
    assert result is not None
    assert abs(result.weight_kg - 73.3) < 0.1


def test_invalid_data_length_returns_none():
    data = bytes(11)
    result = s400_decrypt(data, MAC, BIND_KEY)
    assert result is None


def test_wrong_bind_key_returns_none():
    data = bytes.fromhex("4859d53b2d3314943c58b133638c7457a4000000c3e670dc")
    result = s400_decrypt(data, MAC, "00000000000000000000000000000000")
    assert result is None


def test_invalid_bind_key_length_returns_none():
    data = bytes.fromhex("4859d53b2d3314943c58b133638c7457a4000000c3e670dc")
    result = s400_decrypt(data, MAC, "short")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/leo/documents/code/raspberrypi/google-health-grafana && python -m pytest tests/test_s400_decrypt.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Create scanner package init**

Create `scale/measurement/scanner/__init__.py` (empty file).

- [ ] **Step 4: Implement S400 decryptor**

Create `scale/measurement/scanner/s400_decrypt.py`:

```python
from dataclasses import dataclass
import struct

from Crypto.Cipher import AES


@dataclass(frozen=True)
class S400RawData:
    weight_kg: float
    impedance: float | None
    heart_rate: int | None


def s400_decrypt(
    advertisement_data: bytes,
    mac_address: str,
    bind_key: str,
) -> S400RawData | None:
    if len(bind_key) != 32:
        return None

    if len(advertisement_data) == 26:
        data = advertisement_data[2:]
    elif len(advertisement_data) == 24:
        data = advertisement_data
    else:
        return None

    try:
        mac_bytes = bytes.fromhex(mac_address.replace(":", ""))
        key_bytes = bytes.fromhex(bind_key)
    except ValueError:
        return None

    if len(mac_bytes) != 6 or len(key_bytes) != 16:
        return None

    nonce = (
        mac_bytes[::-1]
        + data[2:5]
        + data[-7:-4]
    )

    mic = data[-4:]
    encrypted_payload = data[5:-7]

    try:
        cipher = AES.new(key_bytes, AES.MODE_CCM, nonce=nonce, mac_len=4)
        cipher.update(b"\x11")
        decrypted = cipher.decrypt_and_verify(encrypted_payload, mic)
    except (ValueError, KeyError):
        return None

    return _parse_decrypted(decrypted)


def _parse_decrypted(decrypted: bytes) -> S400RawData | None:
    if len(decrypted) < 12:
        return None

    obj = decrypted[3:12]
    slice_bytes = obj[1:5]
    value = struct.unpack_from("<I", slice_bytes)[0]

    weight_raw = value & 0x7FF
    heart_rate_raw = (value >> 11) & 0x7F
    impedance_raw = value >> 18

    weight_kg = weight_raw / 10.0
    heart_rate = (heart_rate_raw + 50) if 1 <= heart_rate_raw <= 126 else None
    impedance = (impedance_raw / 10.0) if impedance_raw != 0 and weight_raw != 0 else None

    if weight_kg <= 0:
        return None

    return S400RawData(
        weight_kg=weight_kg,
        impedance=impedance,
        heart_rate=heart_rate,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/leo/documents/code/raspberrypi/google-health-grafana && python -m pytest tests/test_s400_decrypt.py -v`
Expected: all 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add scale/measurement/scanner/ tests/test_s400_decrypt.py
git commit -m "feat(scale): add S400 MiBeacon v5 AES-CCM decryptor

Ported from openScale S400Decryptor.kt. Handles 24/26-byte
advertisement payloads. Extracts weight, impedance, heart rate
from decrypted bit-packed payload. Validated against openScale
test vectors."
```

---

## Task 5: Body Composition Calculator

**Files:**
- Create: `scale/measurement/calculator.py`
- Create: `tests/test_calculator.py`

- [ ] **Step 1: Write failing tests (from openScale MiScaleLibTest.kt)**

Create `tests/test_calculator.py`:

```python
import pytest
from datetime import date

from scale.measurement.model.measurement import Measurement
from scale.measurement.model.user_profile import UserProfile
from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.calculator import calculate_body_composition

TOLERANCE = 0.01


def _make_measurement(weight: float, impedance: float) -> Measurement:
    from datetime import datetime, timezone
    return Measurement(
        weight_kg=weight,
        impedance=impedance,
        heart_rate=None,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_male_30y_180cm_80kg_500ohm():
    profile = UserProfile(name="test", sex="male", height_cm=180,
                          birth_date=date(1996, 1, 1))
    m = _make_measurement(80.0, 500.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert abs(bc.bmi - 24.691) < TOLERANCE
    assert abs(bc.body_fat_pct - 23.315) < TOLERANCE
    assert abs(bc.bone_mass_kg - 3.125) < TOLERANCE
    assert abs(bc.muscle_mass_kg - (40.977 / 100 * 80)) < 0.5
    assert abs(bc.water_pct - 52.606) < TOLERANCE
    assert abs(bc.visceral_fat - 13.36) < TOLERANCE


def test_female_28y_165cm_60kg_520ohm():
    profile = UserProfile(name="test", sex="female", height_cm=165,
                          birth_date=date(1998, 1, 1))
    m = _make_measurement(60.0, 520.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert abs(bc.bmi - 22.039) < TOLERANCE
    assert abs(bc.body_fat_pct - 30.362) < TOLERANCE
    assert abs(bc.bone_mass_kg - 2.487) < TOLERANCE
    assert abs(bc.water_pct - 49.722) < TOLERANCE


def test_male_45y_175cm_95kg_430ohm():
    profile = UserProfile(name="test", sex="male", height_cm=175,
                          birth_date=date(1981, 1, 1))
    m = _make_measurement(95.0, 430.0)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 1, 1))
    assert abs(bc.bmi - 31.020) < TOLERANCE
    assert abs(bc.body_fat_pct - 32.418) < TOLERANCE
    assert abs(bc.bone_mass_kg - 3.273) < TOLERANCE
    assert abs(bc.visceral_fat - 24.462) < TOLERANCE


def test_no_impedance_still_calculates_bmi():
    profile = UserProfile(name="test", sex="male", height_cm=178,
                          birth_date=date(1997, 4, 8))
    m = _make_measurement(80.0, None)
    bc = calculate_body_composition(m, profile, reference_date=date(2026, 5, 18))
    assert abs(bc.bmi - 25.249) < TOLERANCE
    assert bc.weight_kg == 80.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_calculator.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement calculator**

Create `scale/measurement/calculator.py`:

```python
from datetime import date
from scale.measurement.model.measurement import Measurement
from scale.measurement.model.user_profile import UserProfile
from scale.measurement.model.body_composition import BodyComposition


def calculate_body_composition(
    measurement: Measurement,
    profile: UserProfile,
    reference_date: date | None = None,
) -> BodyComposition:
    if reference_date is None:
        reference_date = measurement.timestamp.date()

    weight = measurement.weight_kg
    height = profile.height_cm
    age = profile.age_at(reference_date)
    is_male = profile.sex == "male"
    impedance = measurement.impedance

    bmi = weight / ((height / 100) ** 2)

    if impedance is not None and impedance > 0:
        body_fat_pct = _body_fat(weight, height, age, is_male, impedance)
        water_pct = _water(body_fat_pct)
        bone_mass = _bone_mass(weight, height, age, is_male, impedance)
        lbm = weight - (body_fat_pct * 0.01 * weight) - bone_mass
        if (is_male and weight >= 93.5) or (not is_male and weight >= 84):
            lbm = min(lbm, 120.0)
        muscle_pct = _muscle_pct(height, age, is_male, impedance, weight)
        muscle_mass = muscle_pct / 100.0 * weight
        protein_pct = _protein(muscle_pct, water_pct)
        visceral_fat = _visceral_fat(weight, height, age, is_male)
        bmr = _bmr(weight, height, age, is_male)
        metabolic_age = _metabolic_age(bmr, age)
    else:
        body_fat_pct = 0.0
        water_pct = 0.0
        bone_mass = 0.0
        muscle_mass = 0.0
        protein_pct = 0.0
        visceral_fat = 0.0
        bmr = _bmr(weight, height, age, is_male)
        metabolic_age = age

    ideal_weight = 22.0 * ((height / 100) ** 2)
    body_type = _body_type(body_fat_pct, muscle_mass / weight * 100 if weight > 0 else 0)

    return BodyComposition(
        weight_kg=round(weight, 2),
        bmi=round(bmi, 6),
        body_fat_pct=round(body_fat_pct, 6),
        water_pct=round(water_pct, 6),
        muscle_mass_kg=round(muscle_mass, 2),
        bone_mass_kg=round(bone_mass, 7),
        protein_pct=round(protein_pct, 2),
        visceral_fat=round(visceral_fat, 6),
        bmr_kcal=round(bmr, 2),
        metabolic_age=metabolic_age,
        ideal_weight_kg=round(ideal_weight, 2),
        body_type=body_type,
        heart_rate=measurement.heart_rate,
        impedance=measurement.impedance,
    )


def _lbm_coefficient(weight, height, age, impedance):
    return (
        (height * 9.058 / 100) * (height / 100)
        + weight * 0.32
        + 12.226
        - impedance * 0.0068
        - age * 0.0542
    )


def _body_fat(weight, height, age, is_male, impedance):
    lbm_coeff = _lbm_coefficient(weight, height, age, impedance)
    if not is_male and age <= 49:
        lbm_sub = 9.25
    elif not is_male:
        lbm_sub = 7.25
    else:
        lbm_sub = 0.8

    if is_male and weight < 61:
        coeff = 0.98
    elif not is_male and weight > 60:
        coeff = 0.96
        if height > 160:
            coeff *= 1.03
    elif not is_male and weight < 50:
        coeff = 1.02
        if height > 160:
            coeff *= 1.03
    else:
        coeff = 1.0

    fat_pct = (1.0 - (((lbm_coeff - lbm_sub) * coeff) / weight)) * 100
    return max(0.0, min(fat_pct, 75.0))


def _water(body_fat_pct):
    raw = (100 - body_fat_pct) * 0.7
    coeff = 1.02 if raw < 50 else 0.98
    return raw * coeff


def _bone_mass(weight, height, age, is_male, impedance):
    lbm_coeff = _lbm_coefficient(weight, height, age, impedance)
    base = 0.18016894 if is_male else 0.245691014
    bone = (base - lbm_coeff * 0.05158) * -1

    if bone > 2.2:
        bone += 0.1
    else:
        bone -= 0.1

    if is_male and bone > 5.1:
        bone = 8.0
    elif not is_male and bone > 5.2:
        bone = 8.0

    return max(0.5, bone)


def _muscle_pct(height, age, is_male, impedance, weight):
    sex_val = 1.0 if is_male else 0.0
    if impedance > 0:
        h_m = height / 100.0
        smm = 0.401 * ((h_m * h_m * 10000) / impedance) + 3.825 * sex_val - 0.071 * age + 5.102
        pct = (smm / weight) * 100
        return max(10.0, min(pct, 60.0))
    ratio = 0.52 if is_male else 0.46
    lbm = weight * ratio
    return (lbm / weight) * 100


def _protein(muscle_pct, water_pct):
    return max(0.0, muscle_pct - water_pct)


def _visceral_fat(weight, height, age, is_male):
    if is_male:
        if weight > (13 - (height * 0.5)) * -1:
            vf = (
                ((height * -0.0015) + 0.765) * weight
                + ((height * 0.143) - 12.245) * age
                - 68.8
            ) + height * 0.0806
        else:
            vf = 0.691 + weight * ((height * -0.0024) + 0.8) + age * ((height * 0.0198) - 0.7124) - 6.6
    else:
        if weight > (13 - (height * 0.5)) * -1:
            vf = (
                ((height * -0.0015) + 0.765) * weight
                + ((height * 0.143) - 12.245) * age
                - 68.8
            ) + height * 0.0806 - 18.6
        else:
            vf = 0.691 + weight * ((height * -0.0024) + 0.8) + age * ((height * 0.0198) - 0.7124) - 6.6 - 18.6

    return max(0.0, vf)


def _bmr(weight, height, age, is_male):
    if is_male:
        return 10 * weight + 6.25 * height - 5 * age + 5
    return 10 * weight + 6.25 * height - 5 * age - 161


def _metabolic_age(bmr, age):
    if bmr <= 0:
        return age
    base_bmr = 20.0 * bmr / (10 * 70 + 6.25 * 170 - 5 * 20 + 5)
    estimated = max(15, min(80, int(round(base_bmr))))
    return estimated


def _body_type(fat_pct, muscle_pct):
    if fat_pct < 15:
        fat_level = 0
    elif fat_pct < 25:
        fat_level = 1
    else:
        fat_level = 2

    if muscle_pct < 30:
        muscle_level = 0
    elif muscle_pct < 40:
        muscle_level = 1
    else:
        muscle_level = 2

    return fat_level * 3 + muscle_level + 1
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_calculator.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scale/measurement/calculator.py tests/test_calculator.py
git commit -m "feat(scale): add body composition calculator

Ported from openScale MiScaleLib.kt. Calculates 14 metrics from
weight + impedance + user profile. Formulas: Deurenberg (body fat),
Janssen (muscle mass), Mifflin-St Jeor (BMR). Validated against
openScale test vectors."
```

---

## Task 6: Abstract Protocols

**Files:**
- Create: `scale/measurement/dao.py`
- Create: `scale/measurement/scanner_facade.py`

- [ ] **Step 1: Create scanner facade**

Create `scale/measurement/scanner_facade.py`:

```python
from typing import Protocol, AsyncIterator
from scale.measurement.model.measurement import Measurement


class ScannerFacade(Protocol):
    async def scan(self) -> AsyncIterator[Measurement]:
        ...
```

- [ ] **Step 2: Create measurement DAO**

Create `scale/measurement/dao.py`:

```python
from typing import Protocol
from scale.measurement.model.measurement import Measurement
from scale.measurement.model.body_composition import BodyComposition


class MeasurementDAO(Protocol):
    def persist(self, measurement: Measurement, body_composition: BodyComposition) -> None:
        ...
```

- [ ] **Step 3: Commit**

```bash
git add scale/measurement/dao.py scale/measurement/scanner_facade.py
git commit -m "feat(scale): add abstract protocols for scanner and persistence"
```

---

## Task 7: Measurement Service

**Files:**
- Create: `scale/measurement/service.py`
- Create: `tests/test_service.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_service.py`:

```python
import pytest
from datetime import datetime, date, timezone
from unittest.mock import MagicMock

from scale.measurement.model.measurement import Measurement
from scale.measurement.model.user_profile import UserProfile
from scale.measurement.service import MeasurementService


@pytest.fixture
def profile():
    return UserProfile(name="leo", sex="male", height_cm=178, birth_date=date(1997, 4, 8))


@pytest.fixture
def mock_dao():
    return MagicMock()


@pytest.fixture
def service(profile, mock_dao):
    return MeasurementService(profile=profile, dao=mock_dao)


def _make_measurement(weight=80.0, impedance=500.0, ts=None):
    return Measurement(
        weight_kg=weight,
        impedance=impedance,
        heart_rate=72,
        timestamp=ts or datetime(2026, 5, 18, 10, 0, 0, tzinfo=timezone.utc),
    )


def test_process_measurement_calls_dao(service, mock_dao):
    m = _make_measurement()
    service.process(m)
    mock_dao.persist.assert_called_once()
    args = mock_dao.persist.call_args
    assert args[0][0] == m
    bc = args[0][1]
    assert bc.weight_kg == 80.0
    assert bc.bmi > 0


def test_dedup_within_window(service, mock_dao):
    m1 = _make_measurement(ts=datetime(2026, 5, 18, 10, 0, 0, tzinfo=timezone.utc))
    m2 = _make_measurement(ts=datetime(2026, 5, 18, 10, 0, 15, tzinfo=timezone.utc))
    service.process(m1)
    service.process(m2)
    assert mock_dao.persist.call_count == 1


def test_no_dedup_after_window(service, mock_dao):
    m1 = _make_measurement(ts=datetime(2026, 5, 18, 10, 0, 0, tzinfo=timezone.utc))
    m2 = _make_measurement(ts=datetime(2026, 5, 18, 10, 1, 0, tzinfo=timezone.utc))
    service.process(m1)
    service.process(m2)
    assert mock_dao.persist.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_service.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement service**

Create `scale/measurement/service.py`:

```python
import logging
from datetime import datetime, timezone

from scale.measurement.model.measurement import Measurement
from scale.measurement.model.user_profile import UserProfile
from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.calculator import calculate_body_composition
from scale.measurement.dao import MeasurementDAO

log = logging.getLogger(__name__)


class MeasurementService:
    def __init__(
        self,
        profile: UserProfile,
        dao: MeasurementDAO,
        dedup_window_seconds: int = 30,
    ):
        self._profile = profile
        self._dao = dao
        self._dedup_window = dedup_window_seconds
        self._last_timestamp: datetime | None = None

    def process(self, measurement: Measurement) -> BodyComposition | None:
        if self._is_duplicate(measurement):
            log.debug("Duplicate measurement within dedup window, skipping")
            return None

        bc = calculate_body_composition(measurement, self._profile)

        self._dao.persist(measurement, bc)
        self._last_timestamp = measurement.timestamp

        log.info(
            "Recorded: %.1f kg, %.1f%% fat, %.1f kg muscle, HR=%s",
            bc.weight_kg,
            bc.body_fat_pct,
            bc.muscle_mass_kg,
            bc.heart_rate or "n/a",
        )
        return bc

    def _is_duplicate(self, measurement: Measurement) -> bool:
        if self._last_timestamp is None:
            return False
        delta = abs((measurement.timestamp - self._last_timestamp).total_seconds())
        return delta < self._dedup_window
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_service.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scale/measurement/service.py tests/test_service.py
git commit -m "feat(scale): add measurement service with dedup

Orchestrates scan -> calculate -> persist flow.
30-second dedup window prevents duplicate writes from
repeated BLE broadcasts of the same measurement."
```

---

## Task 8: InfluxDB Writer + Mapper

**Files:**
- Create: `scale/measurement/persistence/__init__.py`
- Create: `scale/measurement/persistence/influx_mapper.py`
- Create: `scale/measurement/persistence/influx_writer.py`
- Create: `tests/test_influx_mapper.py`

- [ ] **Step 1: Write mapper test**

Create `tests/test_influx_mapper.py`:

```python
from datetime import datetime, timezone
from scale.measurement.model.measurement import Measurement
from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.persistence.influx_mapper import to_influx_point


def test_maps_all_fields():
    bc = BodyComposition(
        weight_kg=80.0, bmi=25.2, body_fat_pct=23.3, water_pct=52.6,
        muscle_mass_kg=32.8, bone_mass_kg=3.1, protein_pct=18.5,
        visceral_fat=13.4, bmr_kcal=1780.0, metabolic_age=28,
        ideal_weight_kg=69.6, body_type=5, heart_rate=72, impedance=500.0,
    )
    ts = datetime(2026, 5, 18, 10, 0, 0, tzinfo=timezone.utc)
    point = to_influx_point(bc, ts, user="leo", measurement_name="body_composition")

    line = point.to_line_protocol()
    assert "body_composition" in line
    assert "user=leo" in line
    assert "weight=80.0" in line
    assert "heart_rate=72i" in line
    assert "body_type=5i" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_influx_mapper.py -v`
Expected: FAIL

- [ ] **Step 3: Create persistence package init**

Create `scale/measurement/persistence/__init__.py` (empty file).

- [ ] **Step 4: Implement mapper**

Create `scale/measurement/persistence/influx_mapper.py`:

```python
from datetime import datetime
from influxdb_client import Point
from scale.measurement.model.body_composition import BodyComposition


def to_influx_point(
    bc: BodyComposition,
    timestamp: datetime,
    user: str,
    measurement_name: str = "body_composition",
) -> Point:
    point = (
        Point(measurement_name)
        .tag("user", user)
        .time(timestamp)
        .field("weight", bc.weight_kg)
        .field("bmi", bc.bmi)
        .field("body_fat_pct", bc.body_fat_pct)
        .field("water_pct", bc.water_pct)
        .field("muscle_mass", bc.muscle_mass_kg)
        .field("bone_mass", bc.bone_mass_kg)
        .field("protein_pct", bc.protein_pct)
        .field("visceral_fat", bc.visceral_fat)
        .field("bmr", bc.bmr_kcal)
        .field("metabolic_age", int(bc.metabolic_age))
        .field("ideal_weight", bc.ideal_weight_kg)
        .field("body_type", int(bc.body_type))
    )

    if bc.heart_rate is not None:
        point = point.field("heart_rate", int(bc.heart_rate))
    if bc.impedance is not None:
        point = point.field("impedance", bc.impedance)

    return point
```

- [ ] **Step 5: Run mapper test**

Run: `python -m pytest tests/test_influx_mapper.py -v`
Expected: PASS

- [ ] **Step 6: Implement InfluxDB writer**

Create `scale/measurement/persistence/influx_writer.py`:

```python
import logging
from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

from scale.measurement.model.measurement import Measurement
from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.persistence.influx_mapper import to_influx_point

log = logging.getLogger(__name__)


class InfluxWriter:
    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        user: str,
        measurement_name: str = "body_composition",
    ):
        self._client = InfluxDBClient(url=url, token=token, org=org)
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        self._bucket = bucket
        self._org = org
        self._user = user
        self._measurement_name = measurement_name

    def persist(self, measurement: Measurement, body_composition: BodyComposition) -> None:
        point = to_influx_point(
            body_composition,
            measurement.timestamp,
            user=self._user,
            measurement_name=self._measurement_name,
        )
        self._write_api.write(bucket=self._bucket, org=self._org, record=point)
        log.info("Wrote body composition to InfluxDB")

    def close(self):
        self._client.close()
```

- [ ] **Step 7: Commit**

```bash
git add scale/measurement/persistence/ tests/test_influx_mapper.py
git commit -m "feat(scale): add InfluxDB v2 writer and mapper

InfluxWriter implements MeasurementDAO protocol. Maps 14 body
composition fields to InfluxDB points with user tag. Synchronous
writes to configured bucket."
```

---

## Task 9: BLE Scanner

**Files:**
- Create: `scale/measurement/scanner/ble_scanner.py`

- [ ] **Step 1: Implement BLE scanner**

Create `scale/measurement/scanner/ble_scanner.py`:

```python
import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from scale.measurement.model.measurement import Measurement
from scale.measurement.scanner.s400_decrypt import s400_decrypt

log = logging.getLogger(__name__)

BODY_COMPOSITION_UUID = "0000181b-0000-1000-8000-00805f9b34fb"


class BleScaleScanner:
    def __init__(self, mac_address: str, bind_key: str):
        self._mac = mac_address.upper()
        self._bind_key = bind_key

    async def scan(self) -> AsyncIterator[Measurement]:
        queue: asyncio.Queue[Measurement] = asyncio.Queue()

        def _on_advertisement(device: BLEDevice, adv: AdvertisementData):
            if device.address.upper() != self._mac:
                return

            service_data = adv.service_data
            for uuid, data in service_data.items():
                if "181b" not in uuid.lower():
                    continue

                raw = s400_decrypt(data, self._mac, self._bind_key)
                if raw is None:
                    continue

                measurement = Measurement(
                    weight_kg=raw.weight_kg,
                    impedance=raw.impedance,
                    heart_rate=raw.heart_rate,
                    timestamp=datetime.now(timezone.utc),
                )
                log.info(
                    "BLE: %.1f kg, impedance=%s, hr=%s",
                    raw.weight_kg,
                    raw.impedance,
                    raw.heart_rate,
                )
                queue.put_nowait(measurement)

        scanner = BleakScanner(detection_callback=_on_advertisement)
        await scanner.start()
        log.info("BLE scanning started, waiting for S400 (%s)", self._mac)

        try:
            while True:
                measurement = await queue.get()
                yield measurement
        finally:
            await scanner.stop()
```

- [ ] **Step 2: Commit**

```bash
git add scale/measurement/scanner/ble_scanner.py
git commit -m "feat(scale): add BLE scanner for S400 advertisements

Uses bleak passive scanning filtered by MAC address.
Decrypts MiBeacon v5 service data (UUID 0x181B) and
yields Measurement entities via async iterator."
```

---

## Task 10: Config Loader

**Files:**
- Create: `scale/config.py`
- Create: `scale/config.example.yaml`

- [ ] **Step 1: Create config loader**

Create `scale/config.py`:

```python
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from scale.measurement.model.user_profile import UserProfile


@dataclass(frozen=True)
class ScaleConfig:
    mac: str
    bind_key: str
    scan_timeout: int


@dataclass(frozen=True)
class InfluxConfig:
    url: str
    token: str
    org: str
    bucket: str
    measurement: str


@dataclass(frozen=True)
class AppConfig:
    scale: ScaleConfig
    user: UserProfile
    influx: InfluxConfig
    dedup_window_seconds: int
    log_level: str


def load_config(path: Path = Path("config.yaml")) -> AppConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)

    sc = raw["scale"]
    usr = raw["user"]
    inf = raw["influxdb"]

    return AppConfig(
        scale=ScaleConfig(
            mac=sc["mac"],
            bind_key=sc["bindkey"],
            scan_timeout=sc.get("scan_timeout", 300),
        ),
        user=UserProfile(
            name=usr["name"],
            sex=usr["sex"],
            height_cm=usr["height_cm"],
            birth_date=date.fromisoformat(usr["birth_date"]),
        ),
        influx=InfluxConfig(
            url=inf["url"],
            token=inf["token"],
            org=inf["org"],
            bucket=inf["bucket"],
            measurement=inf.get("measurement", "body_composition"),
        ),
        dedup_window_seconds=raw.get("dedup_window_seconds", 30),
        log_level=raw.get("log_level", "INFO"),
    )
```

- [ ] **Step 2: Create example config**

Create `scale/config.example.yaml`:

```yaml
scale:
  mac: "XX:XX:XX:XX:XX:XX"
  bindkey: "your-32-char-hex-bindkey-here..."
  scan_timeout: 300

user:
  name: leo
  sex: male
  height_cm: 178
  birth_date: "1997-04-08"

influxdb:
  url: "http://localhost:8086"
  token: "your-influxdb-admin-token"
  org: "home"
  bucket: "health"
  measurement: "body_composition"

dedup_window_seconds: 30
log_level: INFO
```

- [ ] **Step 3: Commit**

```bash
git add scale/config.py scale/config.example.yaml
git commit -m "feat(scale): add YAML config loader with example template

Loads scale MAC/bindkey, user profile, InfluxDB v2 connection,
and dedup settings. config.yaml is gitignored; example committed."
```

---

## Task 11: Main Entry Point + Systemd + Requirements

**Files:**
- Create: `scale/main.py`
- Create: `scale/requirements.txt`
- Create: `scale/scale-reader.service`

- [ ] **Step 1: Create main.py**

Create `scale/main.py`:

```python
#!/usr/bin/env python3
import asyncio
import logging
import signal
import sys
from pathlib import Path

from scale.config import load_config
from scale.measurement.service import MeasurementService
from scale.measurement.scanner.ble_scanner import BleScaleScanner
from scale.measurement.persistence.influx_writer import InfluxWriter


async def run():
    config_path = Path(__file__).parent / "config.yaml"
    cfg = load_config(config_path)

    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger(__name__)

    writer = InfluxWriter(
        url=cfg.influx.url,
        token=cfg.influx.token,
        org=cfg.influx.org,
        bucket=cfg.influx.bucket,
        user=cfg.user.name,
        measurement_name=cfg.influx.measurement,
    )

    service = MeasurementService(
        profile=cfg.user,
        dao=writer,
        dedup_window_seconds=cfg.dedup_window_seconds,
    )

    scanner = BleScaleScanner(
        mac_address=cfg.scale.mac,
        bind_key=cfg.scale.bind_key,
    )

    log.info("Scale reader started, scanning for %s", cfg.scale.mac)

    try:
        async for measurement in scanner.scan():
            service.process(measurement)
    except asyncio.CancelledError:
        log.info("Shutting down")
    finally:
        writer.close()


def main():
    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create requirements.txt**

Create `scale/requirements.txt`:

```
bleak>=0.21.0
pycryptodome>=3.20.0
influxdb-client>=1.40.0
pyyaml>=6.0
```

- [ ] **Step 3: Create systemd service**

Create `scale/scale-reader.service`:

```ini
[Unit]
Description=Xiaomi S400 Scale Reader
After=bluetooth.target

[Service]
Type=simple
User=leo
WorkingDirectory=/home/leo/documents/code/raspberrypi/google-health-grafana
ExecStart=/home/leo/documents/code/raspberrypi/google-health-grafana/scale/.venv/bin/python -m scale.main
Restart=always
RestartSec=10
Environment=PYTHONPATH=/home/leo/documents/code/raspberrypi/google-health-grafana

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Commit**

```bash
git add scale/main.py scale/requirements.txt scale/scale-reader.service
git commit -m "feat(scale): add main entry point, requirements, and systemd service

Async main loop: BLE scan -> service.process -> InfluxDB write.
Graceful shutdown on SIGTERM/SIGINT. Systemd unit with restart."
```

---

## Task 12: Grafana Dashboard

**Files:**
- Create: `scale-dashboard.json`

- [ ] **Step 1: Create dashboard JSON**

Create `scale-dashboard.json`:

```json
{
  "dashboard": {
    "title": "Body Composition",
    "uid": "body-composition",
    "timezone": "browser",
    "refresh": "",
    "time": {"from": "now-90d", "to": "now"},
    "templating": {"list": []},
    "panels": [
      {
        "id": 1,
        "title": "Weight",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
        "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
        "fieldConfig": {
          "defaults": {
            "unit": "masskg", "decimals": 1,
            "custom": {"lineWidth": 2, "pointSize": 6, "showPoints": "always", "spanNulls": true, "fillOpacity": 5},
            "color": {"mode": "fixed", "fixedColor": "blue"},
            "thresholds": {"mode": "absolute", "steps": [
              {"color": "transparent", "value": null},
              {"color": "green", "value": 82}
            ]}
          }
        },
        "targets": [
          {"refId": "A", "rawQuery": true, "resultFormat": "time_series",
           "query": "SELECT \"weight\" FROM \"body_composition\" WHERE $timeFilter",
           "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"}}
        ]
      },
      {
        "id": 2,
        "title": "Body Fat %",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
        "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
        "fieldConfig": {
          "defaults": {
            "unit": "percent", "decimals": 1,
            "custom": {"lineWidth": 2, "pointSize": 6, "showPoints": "always", "spanNulls": true, "fillOpacity": 5},
            "color": {"mode": "fixed", "fixedColor": "orange"},
            "thresholds": {"mode": "absolute", "steps": [
              {"color": "green", "value": null},
              {"color": "yellow", "value": 20},
              {"color": "red", "value": 25}
            ]}
          }
        },
        "targets": [
          {"refId": "A", "rawQuery": true, "resultFormat": "time_series",
           "query": "SELECT \"body_fat_pct\" FROM \"body_composition\" WHERE $timeFilter",
           "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"}}
        ]
      },
      {
        "id": 3,
        "title": "Muscle Mass",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8},
        "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
        "fieldConfig": {
          "defaults": {
            "unit": "masskg", "decimals": 1,
            "custom": {"lineWidth": 2, "pointSize": 6, "showPoints": "always", "spanNulls": true, "fillOpacity": 5},
            "color": {"mode": "fixed", "fixedColor": "green"}
          }
        },
        "targets": [
          {"refId": "A", "rawQuery": true, "resultFormat": "time_series",
           "query": "SELECT \"muscle_mass\" FROM \"body_composition\" WHERE $timeFilter",
           "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"}}
        ]
      },
      {
        "id": 4,
        "title": "BMI",
        "type": "gauge",
        "gridPos": {"h": 8, "w": 6, "x": 12, "y": 8},
        "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
        "fieldConfig": {
          "defaults": {
            "min": 15, "max": 40, "decimals": 1,
            "thresholds": {"mode": "absolute", "steps": [
              {"color": "blue", "value": null},
              {"color": "green", "value": 18.5},
              {"color": "yellow", "value": 25},
              {"color": "red", "value": 30}
            ]}
          }
        },
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}},
        "targets": [
          {"refId": "A", "rawQuery": true, "resultFormat": "time_series",
           "query": "SELECT last(\"bmi\") FROM \"body_composition\"",
           "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"}}
        ]
      },
      {
        "id": 5,
        "title": "Heart Rate at Weigh-in",
        "type": "timeseries",
        "gridPos": {"h": 8, "w": 6, "x": 18, "y": 8},
        "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
        "fieldConfig": {
          "defaults": {
            "unit": "bpm", "decimals": 0,
            "custom": {"lineWidth": 2, "pointSize": 6, "showPoints": "always", "spanNulls": true, "fillOpacity": 5},
            "color": {"mode": "fixed", "fixedColor": "red"}
          }
        },
        "targets": [
          {"refId": "A", "rawQuery": true, "resultFormat": "time_series",
           "query": "SELECT \"heart_rate\" FROM \"body_composition\" WHERE $timeFilter",
           "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"}}
        ]
      },
      {
        "id": 6,
        "title": "Latest Reading",
        "type": "table",
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": 16},
        "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"},
        "fieldConfig": {
          "defaults": {"decimals": 1},
          "overrides": [
            {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "custom.width", "value": 200}]},
            {"matcher": {"id": "byName", "options": "body_type"}, "properties": [{"id": "custom.width", "value": 80}]},
            {"matcher": {"id": "byName", "options": "metabolic_age"}, "properties": [{"id": "unit", "value": "short"}]}
          ]
        },
        "targets": [
          {"refId": "A", "rawQuery": true, "resultFormat": "table",
           "query": "SELECT * FROM \"body_composition\" ORDER BY time DESC LIMIT 5",
           "datasource": {"type": "influxdb", "uid": "P9A8567EC67EE4A5C"}}
        ]
      }
    ]
  },
  "overwrite": true
}
```

- [ ] **Step 2: Commit**

```bash
git add scale-dashboard.json
git commit -m "feat: add body composition Grafana dashboard

6 panels: weight trend, body fat % trend, muscle mass trend,
BMI gauge, heart rate at weigh-in, latest readings table.
90-day default range."
```

---

## Task 13: Deploy & Verify

This task is done manually on the Pi.

- [ ] **Step 1: Enable Bluetooth**

```bash
sudo rfkill unblock bluetooth
sudo bluetoothctl power on
```

- [ ] **Step 2: Stop old InfluxDB, start v2**

```bash
cd /home/leo/documents/code/raspberrypi/google-health-grafana
docker compose down
rm -rf influxdb/  # old v1 data (re-backfill from Google Health)
docker compose up -d
```

- [ ] **Step 3: Verify InfluxDB v2 is running**

```bash
curl -s http://localhost:8086/health | python3 -m json.tool
```

Expected: `{"name":"influxdb","message":"ready for queries and writes","status":"pass"}`

- [ ] **Step 4: Set up scale reader venv**

```bash
cd /home/leo/documents/code/raspberrypi/google-health-grafana
python3 -m venv scale/.venv
scale/.venv/bin/pip install -r scale/requirements.txt
```

- [ ] **Step 5: Create config.yaml**

```bash
cp scale/config.example.yaml scale/config.yaml
# Edit scale/config.yaml with actual MAC, bindkey, and InfluxDB token
```

- [ ] **Step 6: Run tests**

```bash
scale/.venv/bin/pip install pytest
PYTHONPATH=. scale/.venv/bin/python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 7: Test scale reader manually**

```bash
PYTHONPATH=. scale/.venv/bin/python -m scale.main
# Step on scale, verify output appears in logs
```

- [ ] **Step 8: Install systemd service**

```bash
sudo cp scale/scale-reader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable scale-reader
sudo systemctl start scale-reader
sudo systemctl status scale-reader
```

- [ ] **Step 9: Bump backfill and verify health-fetch**

In `.env`, set `BACKFILL_DAYS=90` temporarily, then:

```bash
docker compose restart health-fetch
docker compose logs -f health-fetch
```

Verify it syncs successfully with InfluxDB v2.

- [ ] **Step 10: Import Grafana dashboards**

Import both `dashboard.json` (existing health dashboard) and `scale-dashboard.json` via Grafana UI. Update the datasource UID if needed to point to the v2 InfluxDB instance.

- [ ] **Step 11: Verify end-to-end**

Step on the scale. Check:
1. `journalctl -u scale-reader -f` shows the measurement
2. InfluxDB has the data: `curl 'http://localhost:8086/api/v2/query?org=home' --header 'Authorization: Token <token>' --data-raw 'from(bucket:"health") |> range(start: -1h) |> filter(fn: (r) => r._measurement == "body_composition")'`
3. Grafana body composition dashboard shows the data point
