from typing import Protocol
from scale.measurement.model.measurement import Measurement
from scale.measurement.model.body_composition import BodyComposition


class MeasurementDAO(Protocol):
    def persist(
        self, measurement: Measurement, body_composition: BodyComposition
    ) -> None: ...
