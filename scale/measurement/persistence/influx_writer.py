import logging

from influxdb_client_3 import InfluxDBClient3

from scale.measurement.model.body_composition import BodyComposition
from scale.measurement.model.measurement import Measurement
from scale.measurement.persistence.influx_mapper import to_influx_point

log = logging.getLogger(__name__)


class InfluxWriter:
    def __init__(
        self,
        url: str,
        token: str,
        database: str,
        user: str,
        measurement_name: str = "body_composition",
    ) -> None:
        self._client = InfluxDBClient3(host=url, token=token, database=database)
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
