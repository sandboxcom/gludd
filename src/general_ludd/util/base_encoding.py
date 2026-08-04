"""Base encoding utilities supporting multiple alphabets.

Provides base_encode / base_decode with configurable alphabets, plus
pre-defined constants for base32, base32hex, base58, base62, and base85.
"""

from __future__ import annotations

ALPHABET_BASE32 = "abcdefghijklmnopqrstuvwxyz234567"
ALPHABET_BASE32HEX = "0123456789abcdefghijklmnopqrstuv"
ALPHABET_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ALPHABET_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ALPHABET_BASE85 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!#$%&()*+-;<=>?@^_`{|}~"


def _validate_alphabet(alphabet: str) -> None:
    if not isinstance(alphabet, str):
        raise TypeError(f"alphabet must be str, got {type(alphabet).__name__}")
    if len(alphabet) < 2:
        raise ValueError("alphabet must have at least 2 characters")
    if len(alphabet) != len(set(alphabet)):
        raise ValueError("alphabet contains duplicate characters")


def base_encode(data: bytes, alphabet: str) -> str:
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"data must be bytes, got {type(data).__name__}")
    _validate_alphabet(alphabet)
    base = len(alphabet)
    if not data:
        return ""

    leading_zeros = 0
    for byte in data:
        if byte == 0:
            leading_zeros += 1
        else:
            break

    num = int.from_bytes(data, "big")
    if num == 0:
        return alphabet[0] * leading_zeros

    digits: list[str] = []
    while num > 0:
        num, rem = divmod(num, base)
        digits.append(alphabet[rem])

    return (alphabet[0] * leading_zeros) + "".join(reversed(digits))


def base_decode(encoded: str, alphabet: str) -> bytes:
    _validate_alphabet(alphabet)
    base = len(alphabet)
    if not isinstance(encoded, str):
        raise TypeError(f"encoded must be str, got {type(encoded).__name__}")
    if not encoded:
        return b""

    char_map = {c: i for i, c in enumerate(alphabet)}

    leading_zeros = 0
    for ch in encoded:
        if ch == alphabet[0]:
            leading_zeros += 1
        else:
            break

    num = 0
    for ch in encoded:
        if ch not in char_map:
            raise ValueError(f"invalid character {ch!r} for alphabet")
        num = num * base + char_map[ch]

    if num == 0:
        return b"\x00" * leading_zeros

    num_bytes = num.bit_length()
    num_bytes = (num_bytes + 7) // 8
    result = num.to_bytes(num_bytes, "big")
    return b"\x00" * leading_zeros + result
