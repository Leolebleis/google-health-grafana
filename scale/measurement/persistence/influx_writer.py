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

    def persist(
        self, measurement: Measurement, body_composition: BodyComposition
    ) -> None:
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
