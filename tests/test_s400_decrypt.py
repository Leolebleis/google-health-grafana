from scale.measurement.scanner.s400_decrypt import s400_decrypt


MAC_BYTES = bytes.fromhex("84469364A5E6")
KEY_BYTES = bytes.fromhex("58305740b64e4b425e518aa1f4e51339")
WRONG_KEY = bytes.fromhex("00000000000000000000000000000000")


def test_decrypt_24_byte_payload():
    data = bytes.fromhex("4859d53b2d3314943c58b133638c7457a4000000c3e670dc")
    result = s400_decrypt(data, MAC_BYTES, KEY_BYTES)
    assert result is not None
    assert abs(result.weight_kg - 74.2) < 0.1


def test_decrypt_26_byte_payload():
    data = bytes.fromhex("95FE4859D53B3BDE6BC8D05B51C0CDFD9021C9000000925C5039")
    result = s400_decrypt(data, MAC_BYTES, KEY_BYTES)
    assert result is not None
    assert abs(result.weight_kg - 73.2) < 0.1


def test_decrypt_26_byte_payload_variant():
    data = bytes(
        [
            149,
            254,
            72,
            89,
            213,
            59,
            77,
            111,
            53,
            156,
            229,
            111,
            31,
            126,
            126,
            10,
            221,
            220,
            38,
            0,
            0,
            0,
            12,
            19,
            211,
            196,
        ]
    )
    result = s400_decrypt(data, MAC_BYTES, KEY_BYTES)
    assert result is not None
    assert abs(result.weight_kg - 73.3) < 0.1


def test_invalid_data_length_returns_none():
    data = bytes(11)
    result = s400_decrypt(data, MAC_BYTES, KEY_BYTES)
    assert result is None


def test_wrong_bind_key_returns_none():
    data = bytes.fromhex("4859d53b2d3314943c58b133638c7457a4000000c3e670dc")
    result = s400_decrypt(data, MAC_BYTES, WRONG_KEY)
    assert result is None
