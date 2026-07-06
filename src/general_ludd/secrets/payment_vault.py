from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError

logger = logging.getLogger("general_ludd.secrets.payment_vault")

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    _CRYPTO_AVAILABLE = True
except ImportError as _import_err:  # pragma: no cover - exercised only when dep missing
    _CRYPTO_AVAILABLE = False
    # Optional dependency: cryptography may be absent (slim install). The
    # try/except is the documented Python idiom; suppressions are required
    # because mypy sees the imports as already defining these names.
    hashes = None  # type: ignore[assignment]
    AESGCM = None  # type: ignore[assignment, misc]
    HKDF = None  # type: ignore[assignment, misc]


_KEK_PATH = "gludd/payment/kek"
_CARDS_PREFIX = "gludd/payment/cards"
_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PaymentVaultError(RuntimeError):
    """Raised when the payment vault cannot complete an operation."""


def redact_card_number(pan: str) -> str:
    digits = "".join(ch for ch in pan if ch.isdigit())
    last4 = digits[-4:] if len(digits) >= 4 else digits
    return f"**** **** **** {last4}"


def _normalize_pan(card_number: str) -> str:
    return re.sub(r"[\s-]", "", card_number)


def _luhn_valid(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _detect_brand(digits: str) -> str:
    if digits.startswith("4"):
        return "visa"
    if len(digits) >= 2:
        two = digits[:2]
        if two in {"51", "52", "53", "54", "55"} or two in {"22", "23", "24", "25", "26", "27"}:
            return "mastercard"
        if two in {"34", "37"}:
            return "amex"
        if digits.startswith("6011") or digits.startswith("65"):
            return "discover"
    return "unknown"


def _validate_expiry(expiry_month: str, expiry_year: str) -> None:
    try:
        month = int(expiry_month)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expiry_month must be a number 01-12, got {expiry_month!r}") from exc
    if not (1 <= month <= 12):
        raise ValueError(f"expiry_month must be 01-12, got {expiry_month!r}")
    try:
        year = int(expiry_year)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expiry_year must be numeric, got {expiry_year!r}") from exc
    if year >= 100:
        year %= 100
    current_mod = datetime.now(UTC).year % 100
    if year < current_mod:
        raise ValueError(
            f"expiry_year {expiry_year!r} is in the past (current {current_mod:02d})"
        )


class SecurePaymentVault:
    """PCI-DSS style envelope-encrypted payment card vault backed by OpenBao.

    Card primary account numbers (PAN) and CVCs are never stored in cleartext:
    each card payload is encrypted with a per-card AES-256-GCM data encryption
    key (DEK), and the DEK is wrapped by a per-card key encryption key (KEK)
    derived (HKDF-SHA256) from a root key held in OpenBao. Only masked
    metadata (last4, brand, expiry, holder) and the opaque processor token are
    retrievable in cleartext.
    """

    def __init__(self, secrets_manager: SecretsManager) -> None:
        if not _CRYPTO_AVAILABLE:
            raise PaymentVaultError(
                "the 'cryptography' library is required for SecurePaymentVault "
                "(AES-256-GCM + HKDF). Install it with: uv add cryptography "
                "(or pip install cryptography)."
            )
        self._sm = secrets_manager

    def __repr__(self) -> str:
        return "SecurePaymentVault()"

    def store_card(
        self,
        *,
        card_number: str,
        expiry_month: str,
        expiry_year: str,
        cvc: str,
        holder_name: str,
        label: str = "default",
        processor: str = "stripe",
    ) -> str:
        if not isinstance(label, str) or not _LABEL_RE.match(label):
            raise ValueError(f"label must match {_LABEL_RE.pattern!r}, got {label!r}")
        pan = _normalize_pan(card_number)
        if not _luhn_valid(pan):
            raise ValueError("card_number failed Luhn checksum validation")
        if not (isinstance(cvc, str) and cvc.isdigit() and 3 <= len(cvc) <= 4):
            raise ValueError("cvc must be 3 or 4 digits")
        if not isinstance(holder_name, str) or not holder_name.strip():
            raise ValueError("holder_name must be a non-empty string")
        _validate_expiry(expiry_month, expiry_year)
        if not isinstance(processor, str) or not processor.strip():
            raise ValueError("processor must be a non-empty string")

        brand = _detect_brand(pan)
        last4 = pan[-4:]
        token = "tok_" + secrets.token_hex(8)
        processor_token = f"proc_{processor}_" + secrets.token_hex(8)

        logger.debug(
            "storing card label=%s pan=%s brand=%s processor=%s",
            label,
            redact_card_number(pan),
            brand,
            processor,
        )

        try:
            root_key = self._get_or_create_kek_root()
            kek = self._derive_kek(root_key, label)

            dek = os.urandom(32)
            nonce = os.urandom(12)
            plaintext = json.dumps(
                {"card_number": pan, "cvc": cvc}, separators=(",", ":")
            ).encode("utf-8")
            ciphertext = AESGCM(dek).encrypt(nonce, plaintext, None)

            kek_nonce = os.urandom(12)
            encrypted_dek = kek_nonce + AESGCM(kek).encrypt(kek_nonce, dek, None)

            payload: dict[str, Any] = {
                "token": token,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "encrypted_dek": base64.b64encode(encrypted_dek).decode("ascii"),
                "last4": last4,
                "brand": brand,
                "expiry_month": expiry_month,
                "expiry_year": expiry_year,
                "holder_name": holder_name,
                "processor": processor,
                "processor_token": processor_token,
                "stored_at": datetime.now(UTC).isoformat(),
            }
            self._sm.write_secret(self._card_path(label), payload)
        except SecretsUnavailableError as exc:
            raise PaymentVaultError(f"secrets backend unavailable: {exc}") from exc
        except PaymentVaultError:
            raise
        except Exception as exc:
            raise PaymentVaultError(f"failed to store card: {exc}") from exc

        return token

    def get_card_last4(self, label: str = "default") -> str | None:
        data = self._read_card(label)
        if data is None:
            return None
        last4 = data.get("last4")
        return str(last4) if last4 is not None else None

    def get_card_metadata(self, label: str = "default") -> dict[str, Any] | None:
        data = self._read_card(label)
        if data is None:
            return None
        return {
            "token": data.get("token"),
            "last4": data.get("last4"),
            "brand": data.get("brand"),
            "expiry_month": data.get("expiry_month"),
            "expiry_year": data.get("expiry_year"),
            "holder_name": data.get("holder_name"),
            "processor": data.get("processor"),
            "processor_token": data.get("processor_token"),
            "stored_at": data.get("stored_at"),
        }

    def list_cards(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for label in self._iter_card_labels():
            meta = self.get_card_metadata(label)
            if meta is not None:
                meta["label"] = label
                out.append(meta)
        return out

    def delete_card(self, label: str = "default") -> bool:
        path = self._card_path(label)
        try:
            existing = self._sm.read_secret(path)
            if existing is None:
                return False
            self._sm.delete_secret(path)
            return True
        except SecretsUnavailableError as exc:
            raise PaymentVaultError(f"secrets backend unavailable: {exc}") from exc
        except PaymentVaultError:
            raise
        except Exception as exc:
            raise PaymentVaultError(f"failed to delete card: {exc}") from exc

    def get_processor_token(self, label: str = "default") -> str | None:
        data = self._read_card(label)
        if data is None:
            return None
        tok = data.get("processor_token")
        return str(tok) if tok is not None else None

    def _card_path(self, label: str) -> str:
        return f"{_CARDS_PREFIX}/{label}"

    def _read_card(self, label: str) -> dict[str, Any] | None:
        try:
            return self._sm.read_secret(self._card_path(label))
        except SecretsUnavailableError as exc:
            raise PaymentVaultError(f"secrets backend unavailable: {exc}") from exc
        except PaymentVaultError:
            raise
        except Exception as exc:
            raise PaymentVaultError(f"failed to read card: {exc}") from exc

    def _iter_card_labels(self) -> list[str]:
        try:
            keys = self._sm.list_secrets(_CARDS_PREFIX)
        except SecretsUnavailableError as exc:
            raise PaymentVaultError(f"secrets backend unavailable: {exc}") from exc
        except PaymentVaultError:
            raise
        except Exception as exc:
            raise PaymentVaultError(f"failed to list cards: {exc}") from exc

        labels: list[str] = []
        for key in keys:
            label = key[len(_CARDS_PREFIX) + 1:].strip("/") if key.startswith(f"{_CARDS_PREFIX}/") else key.strip("/")
            if not label:
                continue
            label = label.split("/")[0]
            if label == "index":
                continue
            if label not in labels:
                labels.append(label)
        return labels

    def _get_or_create_kek_root(self) -> bytes:
        existing = self._sm.read_secret(_KEK_PATH)
        if existing and "value" in existing:
            return base64.b64decode(str(existing["value"]))
        root = os.urandom(32)
        self._sm.write_secret(_KEK_PATH, {"value": base64.b64encode(root).decode("ascii")})
        logger.debug("auto-generated KEK root at %s", _KEK_PATH)
        return root

    def _derive_kek(self, root_key: bytes, label: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=label.encode("utf-8"),
        ).derive(root_key)
