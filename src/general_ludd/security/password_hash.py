"""Password hashing with argon2id (default), bcrypt, and scrypt.

Provides a uniform interface over three strong password-hashing algorithms:
  - ``hash_password``       — hash a plaintext password (argon2id by default).
  - ``verify_password``     — constant-time verify against a stored hash string.
  - ``needs_rehash``        — check whether a stored hash should be upgraded.
  - ``derive_key``          — raw key derivation for non-password use.

Algorithm selection is encoded in a prefix on the stored hash string
(``$argon2id$…``, ``$2b$…``, ``$scrypt$…``) so ``verify_password`` automatically
dispatches to the correct verifier.  No algorithm guessing or probing.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import VerificationError as Argon2VerificationError

_BCRYPT_AVAILABLE = False
try:
    import bcrypt as _bcrypt

    _BCRYPT_AVAILABLE = True
except ImportError:  # pragma: no cover — build-time guard
    pass

_ARGON2_HASHER = Argon2Hasher()

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_SALT_BYTES = 16
_SCRYPT_HASH_BYTES = 64
_SCRYPT_PREFIX = b"$scrypt$"

_BCRYPT_ROUNDS = 12
_BCRYPT_PREFIX = b"$2b$"


def hash_password(password: str, *, algorithm: str = "argon2id") -> str:
    """Hash *password* and return an encoded hash string.

    *algorithm* must be one of ``"argon2id"``, ``"bcrypt"``, or ``"scrypt"``.
    """
    algo = algorithm.lower()
    if algo == "argon2id":
        return _hash_argon2id(password)
    if algo == "bcrypt":
        return _hash_bcrypt(password)
    if algo == "scrypt":
        return _hash_scrypt(password)
    raise ValueError(f"Unknown algorithm: {algorithm!r}")


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verify *password* against *stored_hash*.

    Automatically dispatches on the algorithm prefix in the hash string.
    Unknown prefixes raise ``ValueError``.
    """
    if stored_hash.startswith("$argon2"):
        return _verify_argon2id(stored_hash, password)
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        return _verify_bcrypt(password, stored_hash)
    if stored_hash.startswith("$scrypt$"):
        return _verify_scrypt(stored_hash, password)
    raise ValueError(f"Unknown hash format: {stored_hash[:16]}...")


def needs_rehash(stored_hash: str) -> bool:
    """Return ``True`` if *stored_hash* was produced with weaker parameters.

    Checks bcrypt rounds, scrypt parameters, and argon2 parameters against
    the current defaults.
    """
    if stored_hash.startswith("$argon2"):
        try:
            _ARGON2_HASHER.check_needs_rehash(stored_hash)
            return False
        except Argon2VerificationError:
            return True
        except Exception:
            return False
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        return _bcrypt_needs_rehash(stored_hash)
    if stored_hash.startswith("$scrypt$"):
        return _scrypt_needs_rehash(stored_hash)
    raise ValueError(f"Unknown hash format: {stored_hash[:16]}...")


def derive_key(
    password: str,
    salt: bytes | None = None,
    *,
    length: int = 32,
    algorithm: str = "scrypt",
) -> tuple[bytes, bytes]:
    """Derive a cryptographic key from *password*.

    Returns ``(derived_key, salt)``.  ``length`` must be at least 16.
    """
    if length < 16:
        raise ValueError(f"key length must be >= 16, got {length}")
    if salt is None:
        salt = os.urandom(32)
    algo = algorithm.lower()
    if algo == "scrypt":
        key = _scrypt_raw(password, salt, length)
    elif algo == "pbkdf2_sha256":
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000, dklen=length)
    else:
        raise ValueError(f"Unknown derivation algorithm: {algorithm!r}")
    return key, salt


# -- argon2id ----------------------------------------------------------------


def _hash_argon2id(password: str) -> str:
    return _ARGON2_HASHER.hash(password)


def _verify_argon2id(stored_hash: str, password: str) -> bool:
    try:
        return _ARGON2_HASHER.verify(stored_hash, password)
    except Argon2VerificationError:
        return False
    except Exception:
        return False


# -- bcrypt ------------------------------------------------------------------


def _hash_bcrypt(password: str) -> str:
    if not _BCRYPT_AVAILABLE:
        raise RuntimeError("bcrypt library not installed")
    raw = password.encode("utf-8")
    return _bcrypt.hashpw(raw, _bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii")


def _verify_bcrypt(password: str, stored_hash: str) -> bool:
    if not _BCRYPT_AVAILABLE:
        raise RuntimeError("bcrypt library not installed")
    try:
        return _bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except Exception:
        return False


def _bcrypt_needs_rehash(stored_hash: str) -> bool:
    if not _BCRYPT_AVAILABLE:
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) < 3:
            return True
        rounds_str = parts[2]
        rounds = int(rounds_str)
        return rounds < _BCRYPT_ROUNDS
    except (ValueError, IndexError):
        return True


# -- scrypt ------------------------------------------------------------------


def _scrypt_raw(password: str, salt: bytes, length: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=length,
    )


def _encode_scrypt_hash(derived: bytes, salt: bytes) -> str:
    import base64

    b64_salt = base64.b64encode(salt).decode("ascii")
    b64_hash = base64.b64encode(derived).decode("ascii")
    return f"$scrypt$ln={_SCRYPT_N},r={_SCRYPT_R},p={_SCRYPT_P}${b64_salt}${b64_hash}"


def _hash_scrypt(password: str) -> str:
    salt = os.urandom(_SCRYPT_SALT_BYTES)
    derived = _scrypt_raw(password, salt, _SCRYPT_HASH_BYTES)
    return _encode_scrypt_hash(derived, salt)


def _verify_scrypt(stored_hash: str, password: str) -> bool:
    parts = stored_hash.split("$")
    if len(parts) != 5 or parts[1] != "scrypt":
        return False
    try:
        import base64

        salt = base64.b64decode(parts[3])
        expected = base64.b64decode(parts[4])
    except Exception:
        return False
    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=len(expected),
        )
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


def _scrypt_needs_rehash(stored_hash: str) -> bool:
    try:
        parts = stored_hash.split("$")
        if len(parts) < 4:
            return True
        params_str = parts[2]
        params: dict[str, int] = {}
        for pair in params_str.split(","):
            k, v = pair.split("=")
            if k == "ln":
                params["n"] = int(v)
            else:
                params[k] = int(v)
        return params.get("n", 0) < _SCRYPT_N or params.get("r", 0) < _SCRYPT_R or params.get("p", 0) < _SCRYPT_P
    except Exception:
        return True
