#!/usr/bin/env python3
import asyncio
import logging
import signal
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
