import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from scale.measurement.scanner.ble_scanner import BleScaleScanner

# Real MAC / key / payload from test_s400_decrypt.py -- decrypt succeeds
_MAC = "84:46:93:64:A5:E6"
_BIND_KEY = "58305740b64e4b425e518aa1f4e51339"
# Valid 24-byte payload that decrypts to ~74.2 kg
_VALID_PAYLOAD = bytes.fromhex("4859d53b2d3314943c58b133638c7457a4000000c3e670dc")

_OTHER_MAC = "AA:BB:CC:DD:EE:FF"


def _make_adv(service_data: dict) -> MagicMock:
    adv = MagicMock()
    adv.service_data = service_data
    return adv


def _make_device(address: str) -> MagicMock:
    device = MagicMock()
    device.address = address
    return device


@pytest.fixture
def scanner() -> BleScaleScanner:
    return BleScaleScanner(mac_address=_MAC, bind_key=_BIND_KEY)


@patch("scale.measurement.scanner.ble_scanner.BleakScanner")
async def test_scan_yields_measurement_for_matching_mac(mock_bleak_cls: MagicMock, scanner: BleScaleScanner) -> None:
    """A valid advertisement from the target MAC is yielded as a Measurement."""
    mock_bleak = AsyncMock()
    captured: dict = {}

    def _capture_scanner(**kwargs: object) -> AsyncMock:
        captured["cb"] = kwargs.get("detection_callback")
        return mock_bleak

    mock_bleak_cls.side_effect = _capture_scanner

    gen = scanner.scan()

    # Start the generator up to the first `await scanner.start()` call
    # by scheduling a task and letting the event loop tick.
    task = asyncio.ensure_future(gen.__anext__())

    # Give the event loop a chance to run up to `await queue.get()`
    await asyncio.sleep(0)

    # Fire the advertisement callback with the real MAC and valid payload
    cb = captured["cb"]
    assert cb is not None
    cb(_make_device(_MAC), _make_adv({"0000181b-0000-1000-8000-00805f9b34fb": _VALID_PAYLOAD}))

    # Now the queue has an item; the task should complete
    measurement = await task
    assert measurement is not None
    assert abs(measurement.weight_kg - 74.2) < 0.1

    # Clean up -- cancel the generator so `finally` runs (stops scanner)
    await gen.aclose()
    mock_bleak.stop.assert_awaited()


@patch("scale.measurement.scanner.ble_scanner.BleakScanner")
async def test_scan_ignores_non_matching_mac(mock_bleak_cls: MagicMock, scanner: BleScaleScanner) -> None:
    """An advertisement from a different MAC address is silently ignored."""
    mock_bleak = AsyncMock()
    captured: dict = {}

    def _capture_scanner(**kwargs: object) -> AsyncMock:
        captured["cb"] = kwargs.get("detection_callback")
        return mock_bleak

    mock_bleak_cls.side_effect = _capture_scanner

    gen = scanner.scan()
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)

    cb = captured["cb"]
    # Fire with the WRONG MAC -- should be ignored, queue stays empty
    cb(_make_device(_OTHER_MAC), _make_adv({"svc": _VALID_PAYLOAD}))

    # Task should still be pending (queue is empty)
    assert not task.done()

    task.cancel()
    with pytest.raises((asyncio.CancelledError, StopAsyncIteration)):
        await task

    await gen.aclose()


@patch("scale.measurement.scanner.ble_scanner.BleakScanner")
async def test_scan_skips_undecryptable_payload(mock_bleak_cls: MagicMock, scanner: BleScaleScanner) -> None:
    """A payload that fails decryption (wrong length) does not enqueue a measurement."""
    mock_bleak = AsyncMock()
    captured: dict = {}

    def _capture_scanner(**kwargs: object) -> AsyncMock:
        captured["cb"] = kwargs.get("detection_callback")
        return mock_bleak

    mock_bleak_cls.side_effect = _capture_scanner

    gen = scanner.scan()
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)

    cb = captured["cb"]
    # 11-byte payload -- s400_decrypt returns None for invalid lengths
    cb(_make_device(_MAC), _make_adv({"svc": bytes(11)}))

    assert not task.done()

    task.cancel()
    with pytest.raises((asyncio.CancelledError, StopAsyncIteration)):
        await task

    await gen.aclose()
