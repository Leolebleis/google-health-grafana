import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from scale.measurement.model.measurement import Measurement
from scale.measurement.scanner.s400_decrypt import s400_decrypt

log = logging.getLogger(__name__)


class BleScaleScanner:
    def __init__(self, mac_address: str, bind_key: str) -> None:
        self._mac = mac_address.upper()
        self._mac_bytes = bytes.fromhex(mac_address.replace(":", ""))
        self._key_bytes = bytes.fromhex(bind_key)

    async def scan(self) -> AsyncIterator[Measurement]:
        queue: asyncio.Queue[Measurement] = asyncio.Queue()

        def _on_advertisement(device: BLEDevice, adv: AdvertisementData) -> None:
            if device.address.upper() != self._mac:
                return

            for data in adv.service_data.values():
                raw = s400_decrypt(data, self._mac_bytes, self._key_bytes)
                if raw is None:
                    continue

                measurement = Measurement(
                    weight_kg=raw.weight_kg,
                    impedance=raw.impedance,
                    heart_rate=raw.heart_rate,
                    timestamp=datetime.now(UTC),
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
