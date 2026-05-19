from datetime import date
from pathlib import Path

import pytest
from scale.config import load_config


@pytest.fixture
def config_yaml(tmp_path: Path) -> Path:
    content = """\
scale:
  mac: "AA:BB:CC:DD:EE:FF"
  bindkey: "deadbeefdeadbeefdeadbeefdeadbeef"

user:
  name: "alice"
  sex: "female"
  height_cm: 165
  birth_date: "1995-06-15"

influxdb:
  url: "http://localhost:8181"
  token: "my-token"
  database: "health"
  measurement: "body_composition"

dedup_window_seconds: 60
log_level: "DEBUG"
"""
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


@pytest.fixture
def config_yaml_defaults(tmp_path: Path) -> Path:
    """Minimal config -- omits optional fields to exercise defaults."""
    content = """\
scale:
  mac: "11:22:33:44:55:66"
  bindkey: "aabbccddeeff00112233445566778899"

user:
  name: "bob"
  sex: "male"
  height_cm: 180
  birth_date: "1990-01-01"

influxdb:
  url: "http://influx:8181"
  token: "tok"
  database: "bucket"
"""
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


def test_full_config_parses_correctly(config_yaml: Path) -> None:
    cfg = load_config(config_yaml)

    assert cfg.scale.mac == "AA:BB:CC:DD:EE:FF"
    assert cfg.scale.bind_key == "deadbeefdeadbeefdeadbeefdeadbeef"

    assert cfg.user.name == "alice"
    assert cfg.user.sex == "female"
    assert cfg.user.height_cm == 165
    assert cfg.user.birth_date == date(1995, 6, 15)

    assert cfg.influx.url == "http://localhost:8181"
    assert cfg.influx.token == "my-token"
    assert cfg.influx.database == "health"
    assert cfg.influx.measurement == "body_composition"

    assert cfg.dedup_window_seconds == 60
    assert cfg.log_level == "DEBUG"


def test_defaults_when_optional_fields_omitted(config_yaml_defaults: Path) -> None:
    cfg = load_config(config_yaml_defaults)

    assert cfg.influx.measurement == "body_composition"
    assert cfg.dedup_window_seconds == 30
    assert cfg.log_level == "INFO"


def test_scale_config_fields(config_yaml: Path) -> None:
    cfg = load_config(config_yaml)
    assert cfg.scale.mac == "AA:BB:CC:DD:EE:FF"
    assert cfg.scale.bind_key == "deadbeefdeadbeefdeadbeefdeadbeef"


def test_user_profile_age_calculation(config_yaml_defaults: Path) -> None:
    cfg = load_config(config_yaml_defaults)
    assert cfg.user.birth_date == date(1990, 1, 1)
    assert cfg.user.age_at(date(2026, 1, 1)) == 36
