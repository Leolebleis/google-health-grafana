from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Measurement:
    weight_kg: float
    impedance: float | None
    heart_rate: int | None
    timestamp: datetime
