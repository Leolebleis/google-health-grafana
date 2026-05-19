from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from scale.measurement.model.user_profile import UserProfile


@dataclass(frozen=True)
class ScaleConfig:
    mac: str
    bind_key: str


@dataclass(frozen=True)
class InfluxConfig:
    url: str
    token: str
    database: str
    measurement: str


@dataclass(frozen=True)
class AppConfig:
    scale: ScaleConfig
    user: UserProfile
    influx: InfluxConfig
    dedup_window_seconds: int
    log_level: str


def load_config(path: Path = Path("config.yaml")) -> AppConfig:
    with path.open() as f:
        raw = yaml.safe_load(f)

    sc = raw["scale"]
    usr = raw["user"]
    inf = raw["influxdb"]

    return AppConfig(
        scale=ScaleConfig(
            mac=sc["mac"],
            bind_key=sc["bindkey"],
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
            database=inf["database"],
            measurement=inf.get("measurement", "body_composition"),
        ),
        dedup_window_seconds=raw.get("dedup_window_seconds", 30),
        log_level=raw.get("log_level", "INFO"),
    )
