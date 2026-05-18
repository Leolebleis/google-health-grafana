from scale.measurement.scanner.s400_decrypt import s400_decrypt


MAC = "84:46:93:64:A5:E6"
BIND_KEY = "58305740b64e4b425e518aa1f4e51339"


def test_decrypt_24_byte_payload():
    data = bytes.fromhex("4859d53b2d3314943c58b133638c7457a4000000c3e670dc")
    result = s400_decrypt(data, MAC, BIND_KEY)
    assert result is not None
    assert abs(result.weight_kg - 74.2) < 0.1


def test_decrypt_26_byte_payload():
    data = bytes.fromhex("95FE4859D53B3BDE6BC8D05B51C0CDFD9021C9000000925C5039")
    result = s400_decrypt(data, MAC, BIND_KEY)
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
    result = s400_decrypt(data, MAC, BIND_KEY)
    assert result is not None
    assert abs(result.weight_kg - 73.3) < 0.1


def test_invalid_data_length_returns_none():
    data = bytes(11)
    result = s400_decrypt(data, MAC, BIND_KEY)
    assert result is None


def test_wrong_bind_key_returns_none():
    data = bytes.fromhex("4859d53b2d3314943c58b133638c7457a4000000c3e670dc")
    result = s400_decrypt(data, MAC, "00000000000000000000000000000000")
    assert result is None


def test_invalid_bind_key_length_returns_none():
    data = bytes.fromhex("4859d53b2d3314943c58b133638c7457a4000000c3e670dc")
    result = s400_decrypt(data, MAC, "short")
    assert result is None
