import struct
from dataclasses import dataclass

from Crypto.Cipher import AES

_DATA_LEN = 24
_DATA_LEN_WITH_HEADER = 26
_MIN_DECRYPTED_LEN = 12
_MAX_HEART_RATE_RAW = 126


@dataclass(frozen=True)
class S400RawData:
    weight_kg: float
    impedance: float | None
    heart_rate: int | None


def s400_decrypt(
    advertisement_data: bytes,
    mac_bytes: bytes,
    key_bytes: bytes,
) -> S400RawData | None:
    if len(advertisement_data) == _DATA_LEN_WITH_HEADER:
        data = advertisement_data[2:]
    elif len(advertisement_data) == _DATA_LEN:
        data = advertisement_data
    else:
        return None

    nonce = mac_bytes[::-1] + data[2:5] + data[-7:-4]

    mic = data[-4:]
    encrypted_payload = data[5:-7]

    try:
        cipher = AES.new(key_bytes, AES.MODE_CCM, nonce=nonce, mac_len=4)
        cipher.update(b"\x11")
        decrypted = cipher.decrypt_and_verify(encrypted_payload, mic)
    except (ValueError, KeyError):
        return None

    return _parse_decrypted(decrypted)


def _parse_decrypted(decrypted: bytes) -> S400RawData | None:
    if len(decrypted) < _MIN_DECRYPTED_LEN:
        return None

    obj = decrypted[3:12]
    slice_bytes = obj[1:5]
    value = struct.unpack_from("<I", slice_bytes)[0]

    weight_raw = value & 0x7FF
    heart_rate_raw = (value >> 11) & 0x7F
    impedance_raw = value >> 18

    weight_kg = weight_raw / 10.0
    heart_rate = (heart_rate_raw + 50) if 1 <= heart_rate_raw <= _MAX_HEART_RATE_RAW else None
    impedance = (impedance_raw / 10.0) if impedance_raw != 0 and weight_raw != 0 else None

    if weight_kg <= 0:
        return None

    return S400RawData(
        weight_kg=weight_kg,
        impedance=impedance,
        heart_rate=heart_rate,
    )
