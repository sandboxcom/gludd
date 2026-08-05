"""Move-to-Front (MTF) transform.

An invertible transform used in data compression (e.g. Burrows-Wheeler + MTF).
For each input symbol the encoder outputs its current position in the alphabet
and then moves that symbol to the front.  The decoder inverts this process.
"""

from __future__ import annotations


def mtf_encode(data: bytes, alphabet: list[int]) -> list[int]:
    if not alphabet:
        raise ValueError("alphabet must be non-empty")
    alpha = alphabet[:]
    result: list[int] = []
    for sym in data:
        try:
            idx = alpha.index(sym)
        except ValueError:
            raise ValueError(f"symbol {sym!r} not in alphabet") from None
        result.append(idx)
        del alpha[idx]
        alpha.insert(0, sym)
    return result


def mtf_decode(indices: list[int], alphabet: list[int]) -> bytes:
    if not alphabet:
        raise ValueError("alphabet must be non-empty")
    alpha = alphabet[:]
    result: list[int] = []
    for idx in indices:
        if idx < 0 or idx >= len(alpha):
            raise ValueError(f"index {idx} out of range for alphabet of size {len(alpha)}")
        sym = alpha[idx]
        result.append(sym)
        del alpha[idx]
        alpha.insert(0, sym)
    return bytes(result)
