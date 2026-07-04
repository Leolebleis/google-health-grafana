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


async def _start_scan(mock_bleak_cls: MagicMock, scanner: BleScaleScanner) -> tuple:
    """Start scanner.scan() against a mocked BleakScanner.

    Returns (mock_bleak, detection_callback, generator, first-yield task) with
    the generator parked on `await queue.get()`.
    """
    mock_bleak = AsyncMock()
    captured: dict = {}

    def _capture_scanner(**kwargs: object) -> AsyncMock:
        captured["cb"] = kwargs.get("detection_callback")
        return mock_bleak

    mock_bleak_cls.side_effect = _capture_scanner

    gen = scanner.scan()
    task = asyncio.ensure_future(gen.__anext__())
    # Give the event loop a chance to run up to `await queue.get()`
    await asyncio.sleep(0)
    return mock_bleak, captured["cb"], gen, task


async def _assert_nothing_yielded(task, gen):
    assert not task.done()
    task.cancel()
    with pytest.raises((asyncio.CancelledError, StopAsyncIteration)):
        await task
    await gen.aclose()


@patch("scale.measurement.scanner.ble_scanner.BleakScanner")
async def test_scan_yields_measurement_for_matching_mac(mock_bleak_cls: MagicMock, scanner: BleScaleScanner) -> None:
    """A valid advertisement from the target MAC is yielded as a Measurement."""
    mock_bleak, cb, gen, task = await _start_scan(mock_bleak_cls, scanner)

    cb(_make_device(_MAC), _make_adv({"0000181b-0000-1000-8000-00805f9b34fb": _VALID_PAYLOAD}))

    measurement = await task
    assert measurement is not None
    assert abs(measurement.weight_kg - 74.2) < 0.1

    # Clean up -- close the generator so `finally` runs (stops scanner)
    await gen.aclose()
    mock_bleak.stop.assert_awaited()


@patch("scale.measurement.scanner.ble_scanner.BleakScanner")
async def test_scan_ignores_non_matching_mac(mock_bleak_cls: MagicMock, scanner: BleScaleScanner) -> None:
    """An advertisement from a different MAC address is silently ignored."""
    _, cb, gen, task = await _start_scan(mock_bleak_cls, scanner)

    cb(_make_device(_OTHER_MAC), _make_adv({"svc": _VALID_PAYLOAD}))

    await _assert_nothing_yielded(task, gen)


@patch("scale.measurement.scanner.ble_scanner.BleakScanner")
async def test_scan_warns_on_measurement_sized_frame_that_fails_decrypt(
    mock_bleak_cls: MagicMock, scanner: BleScaleScanner, caplog: pytest.LogCaptureFixture
) -> None:
    """A 24-byte frame that fails decryption logs a warning instead of failing silently."""
    _, cb, gen, task = await _start_scan(mock_bleak_cls, scanner)

    # 24-byte garbage payload -- measurement-sized, but MIC check fails
    with caplog.at_level("WARNING"):
        cb(_make_device(_MAC), _make_adv({"svc": bytes(24)}))

    assert "failed decrypt" in caplog.text
    await _assert_nothing_yielded(task, gen)


@patch("scale.measurement.scanner.ble_scanner.BleakScanner")
async def test_scan_skips_undecryptable_payload(mock_bleak_cls: MagicMock, scanner: BleScaleScanner) -> None:
    """A payload that fails decryption (wrong length) does not enqueue a measurement."""
    _, cb, gen, task = await _start_scan(mock_bleak_cls, scanner)

    # 11-byte payload -- s400_decrypt returns None for invalid lengths
    cb(_make_device(_MAC), _make_adv({"svc": bytes(11)}))

    await _assert_nothing_yielded(task, gen)
