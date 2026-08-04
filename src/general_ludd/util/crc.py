"""CRC and checksum algorithms: CRC32, CRC32C, Adler32, Fletcher16/32,
XOR, and Internet Checksum (RFC 1071). Pure-Python, stdlib only.
"""

from __future__ import annotations

_CRC32_TABLE: list[int] = []
_CRC32C_TABLE: list[int] = []


def _make_crc32_table() -> list[int]:
    if _CRC32_TABLE:
        return _CRC32_TABLE
    table: list[int] = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1
        table.append(crc)
    _CRC32_TABLE.extend(table)
    return _CRC32_TABLE


def _make_crc32c_table() -> list[int]:
    if _CRC32C_TABLE:
        return _CRC32C_TABLE
    table: list[int] = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x82F63B78
            else:
                crc >>= 1
        table.append(crc)
    _CRC32C_TABLE.extend(table)
    return _CRC32C_TABLE


# ── CRC32 ─────────────────────────────────────────────────────────────────────


class CRC32:
    """IEEE 802.3 CRC-32 checksum.

    Polynomial: 0xEDB88320 (reflected). Initial value: 0xFFFFFFFF.
    Final XOR: 0xFFFFFFFF. Matches zlib.crc32, gzip, PNG, etc.
    """

    _table: list[int] | None = None

    def __init__(self, value: int = 0) -> None:
        if CRC32._table is None:
            CRC32._table = _make_crc32_table()
        self._crc: int = value ^ 0xFFFFFFFF

    def update(self, data: bytes) -> None:
        crc = self._crc
        table = CRC32._table
        assert table is not None
        for byte in data:
            crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
        self._crc = crc

    def digest(self) -> int:
        return self._crc ^ 0xFFFFFFFF

    def hexdigest(self) -> str:
        return f"{self.digest():08x}"

    @staticmethod
    def compute(data: bytes, initial: int = 0) -> int:
        crc = CRC32(initial)
        crc.update(data)
        return crc.digest()


# ── CRC32C ────────────────────────────────────────────────────────────────────


class CRC32C:
    """Castagnoli CRC-32C checksum (iSCSI, SCTP, ext4).

    Polynomial: 0x82F63B78 (reflected). Initial value: 0xFFFFFFFF.
    Final XOR: 0xFFFFFFFF.
    """

    _table: list[int] | None = None

    def __init__(self, value: int = 0) -> None:
        if CRC32C._table is None:
            CRC32C._table = _make_crc32c_table()
        self._crc: int = value ^ 0xFFFFFFFF

    def update(self, data: bytes) -> None:
        crc = self._crc
        table = CRC32C._table
        assert table is not None
        for byte in data:
            crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
        self._crc = crc

    def digest(self) -> int:
        return self._crc ^ 0xFFFFFFFF

    def hexdigest(self) -> str:
        return f"{self.digest():08x}"

    @staticmethod
    def compute(data: bytes, initial: int = 0) -> int:
        crc = CRC32C(initial)
        crc.update(data)
        return crc.digest()


# ── Adler32 ───────────────────────────────────────────────────────────────────

_ADLER_MOD = 65521


class Adler32:
    """Adler-32 rolling checksum (zlib, rsync).

    Uses two 16-bit running sums: s1 (sum of bytes) and s2 (sum of s1 values).
    Modulo 65521 (largest prime < 2^16).
    """

    def __init__(self, s1: int = 1, s2: int = 0) -> None:
        self._s1: int = s1 & 0xFFFF
        self._s2: int = s2 & 0xFFFF

    def update(self, data: bytes) -> None:
        s1 = self._s1
        s2 = self._s2
        for byte in data:
            s1 = (s1 + byte) % _ADLER_MOD
            s2 = (s2 + s1) % _ADLER_MOD
        self._s1 = s1
        self._s2 = s2

    def digest(self) -> int:
        return (self._s2 << 16) | self._s1

    def hexdigest(self) -> str:
        return f"{self.digest():08x}"

    @staticmethod
    def compute(data: bytes) -> int:
        a = Adler32()
        a.update(data)
        return a.digest()

    @property
    def s1(self) -> int:
        return self._s1

    @property
    def s2(self) -> int:
        return self._s2

    def rolling_out(self, out_byte: int, window_size: int) -> None:
        s1 = (self._s1 - out_byte + _ADLER_MOD) % _ADLER_MOD
        s2 = (self._s2 - window_size * out_byte - 1 + _ADLER_MOD * window_size) % _ADLER_MOD
        self._s1 = s1
        self._s2 = s2


# ── Fletcher ──────────────────────────────────────────────────────────────────


class Fletcher16:
    """Fletcher-16 checksum.

    Two running 8-bit sums modulo 256. Produces a 16-bit value.
    """

    def __init__(self) -> None:
        self._sum1: int = 0
        self._sum2: int = 0

    def update(self, data: bytes) -> None:
        s1 = self._sum1
        s2 = self._sum2
        for byte in data:
            s1 = (s1 + byte) & 0xFF
            s2 = (s2 + s1) & 0xFF
        self._sum1 = s1
        self._sum2 = s2

    def digest(self) -> int:
        return (self._sum2 << 8) | self._sum1

    def hexdigest(self) -> str:
        return f"{self.digest():04x}"

    @staticmethod
    def compute(data: bytes) -> int:
        f = Fletcher16()
        f.update(data)
        return f.digest()


class Fletcher32:
    """Fletcher-32 checksum.

    Two running 16-bit sums modulo 65535. Produces a 32-bit value.
    """

    _MOD = 65535

    def __init__(self) -> None:
        self._sum1: int = 0
        self._sum2: int = 0

    def update(self, data: bytes) -> None:
        s1 = self._sum1
        s2 = self._sum2
        i = 0
        n = len(data)
        while i < n:
            block_len = min(n - i, 360)
            for j in range(block_len):
                s1 += data[i + j]
                s2 += s1
            s1 %= self._MOD
            s2 %= self._MOD
            i += block_len
        self._sum1 = s1
        self._sum2 = s2

    def digest(self) -> int:
        return (self._sum2 << 16) | self._sum1

    def hexdigest(self) -> str:
        return f"{self.digest():08x}"

    @staticmethod
    def compute(data: bytes) -> int:
        f = Fletcher32()
        f.update(data)
        return f.digest()


# ── XOR ───────────────────────────────────────────────────────────────────────


def xor8_checksum(data: bytes) -> int:
    r = 0
    for b in data:
        r ^= b
    return r


def xor16_checksum(data: bytes) -> int:
    r = 0
    for i in range(0, len(data) - 1, 2):
        r ^= (data[i] << 8) | data[i + 1]
    if len(data) & 1:
        r ^= data[-1] << 8
    return r & 0xFFFF


def xor32_checksum(data: bytes) -> int:
    r = 0
    for i in range(0, len(data) - 3, 4):
        r ^= int.from_bytes(data[i : i + 4], "big")
    rem = len(data) & 3
    if rem:
        tail = data[-rem:] + b"\x00" * (4 - rem)
        r ^= int.from_bytes(tail, "big")
    return r & 0xFFFFFFFF


# ── Internet Checksum ─────────────────────────────────────────────────────────


def internet_checksum(data: bytes) -> int:
    """RFC 1071 Internet checksum — 16-bit one's complement sum.

    Returns the one's complement of the one's complement sum of 16-bit words.
    If data length is odd, a final zero byte is appended for padding.
    """
    total = 0
    length = len(data)
    i = 0
    while i < length - 1:
        word = (data[i] << 8) | data[i + 1]
        total += word
        i += 2
    if i < length:
        total += data[i] << 8
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def internet_checksum_verify(data: bytes, received_checksum: int) -> bool:
    total = 0
    length = len(data)
    i = 0
    while i < length - 1:
        total += (data[i] << 8) | data[i + 1]
        i += 2
    if i < length:
        total += data[i] << 8
    total += received_checksum
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (total & 0xFFFF) == 0xFFFF
