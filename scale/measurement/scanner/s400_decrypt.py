from dataclasses import dataclass
import struct

from Crypto.Cipher import AES


@dataclass(frozen=True)
class S400RawData:
    weight_kg: float
    impedance: float | None
    heart_rate: int | None


def s400_decrypt(
    advertisement_data: bytes,
    mac_address: str,
    bind_key: str,
) -> S400RawData | None:
    if len(bind_key) != 32:
        return None

    if len(advertisement_data) == 26:
        data = advertisement_data[2:]
    elif len(advertisement_data) == 24:
        data = advertisement_data
    else:
        return None

    try:
        mac_bytes = bytes.fromhex(mac_address.replace(":", ""))
        key_bytes = bytes.fromhex(bind_key)
    except ValueError:
        return None

    if len(mac_bytes) != 6 or len(key_bytes) != 16:
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
    if len(decrypted) < 12:
        return None

    obj = decrypted[3:12]
    slice_bytes = obj[1:5]
    value = struct.unpack_from("<I", slice_bytes)[0]

    weight_raw = value & 0x7FF
    heart_rate_raw = (value >> 11) & 0x7F
    impedance_raw = value >> 18

    weight_kg = weight_raw / 10.0
    heart_rate = (heart_rate_raw + 50) if 1 <= heart_rate_raw <= 126 else None
    impedance = (
        (impedance_raw / 10.0) if impedance_raw != 0 and weight_raw != 0 else None
    )

    if weight_kg <= 0:
        return None

    return S400RawData(
        weight_kg=weight_kg,
        impedance=impedance,
        heart_rate=heart_rate,
    )
