"""Validation and backend-failure contracts for the payment vault."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

import general_ludd.secrets.payment_vault as payment_vault
from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError
from general_ludd.secrets.payment_vault import PaymentVaultError, SecurePaymentVault

VISA = "4111111111111111"


def _vault(manager: MagicMock | None = None) -> tuple[SecurePaymentVault, MagicMock]:
    secrets_manager = manager or MagicMock(spec=SecretsManager)
    return SecurePaymentVault(secrets_manager), secrets_manager


def _store(vault: SecurePaymentVault, **overrides: object) -> str:
    values: dict[str, object] = {
        "card_number": VISA,
        "expiry_month": "12",
        "expiry_year": "99",
        "cvc": "123",
        "holder_name": "Ada Lovelace",
        "label": "default",
        "processor": "stripe",
    }
    values.update(overrides)
    return vault.store_card(
        card_number=str(values["card_number"]),
        expiry_month=str(values["expiry_month"]),
        expiry_year=str(values["expiry_year"]),
        cvc=str(values["cvc"]),
        holder_name=str(values["holder_name"]),
        label=str(values["label"]),
        processor=str(values["processor"]),
    )


def test_luhn_and_brand_helpers_cover_rejection_and_unknown_brands() -> None:
    assert payment_vault._luhn_valid("not-digits") is False
    assert payment_vault._luhn_valid("1") is False
    assert payment_vault._detect_brand("6011000990139424") == "discover"
    assert payment_vault._detect_brand("6500000000000002") == "discover"
    assert payment_vault._detect_brand("0000000000000000") == "unknown"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"label": "invalid label"}, "label must match"),
        ({"cvc": "12"}, "cvc must be 3 or 4 digits"),
        ({"cvc": "abc"}, "cvc must be 3 or 4 digits"),
        ({"holder_name": ""}, "holder_name must be a non-empty string"),
        ({"processor": ""}, "processor must be a non-empty string"),
        ({"expiry_month": "month"}, "expiry_month must be a number"),
        ({"expiry_month": "0"}, "expiry_month must be 01-12"),
        ({"expiry_year": "year"}, "expiry_year must be numeric"),
    ],
)
def test_store_rejects_invalid_public_inputs(
    overrides: dict[str, object],
    message: str,
) -> None:
    vault, _manager = _vault()

    with pytest.raises(ValueError, match=message):
        _store(vault, **overrides)


def test_four_digit_expiry_year_is_normalized_before_comparison() -> None:
    future_year = datetime.now(UTC).year + 1

    payment_vault._validate_expiry("12", str(future_year))


def test_vault_requires_crypto_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payment_vault, "_CRYPTO_AVAILABLE", False)

    with pytest.raises(PaymentVaultError, match="cryptography"):
        SecurePaymentVault(MagicMock(spec=SecretsManager))


def test_repr_and_missing_metadata_are_secret_free() -> None:
    vault, manager = _vault()
    manager.read_secret.return_value = None

    assert repr(vault) == "SecurePaymentVault()"
    assert vault.get_card_metadata() is None
    assert vault.get_processor_token() is None
    assert vault.get_card_last4() is None


def test_missing_processor_token_remains_none() -> None:
    vault, manager = _vault()
    manager.read_secret.return_value = {"last4": "1111"}

    assert vault.get_processor_token() is None


@pytest.mark.parametrize("operation", ["read", "delete", "list"])
def test_backend_unavailable_errors_are_redacted(operation: str) -> None:
    manager = MagicMock(spec=SecretsManager)
    vault = SecurePaymentVault(manager)
    error = SecretsUnavailableError("backend offline")
    if operation == "read":
        manager.read_secret.side_effect = error
        with pytest.raises(PaymentVaultError, match="secrets backend unavailable"):
            vault.get_card_metadata()
    elif operation == "delete":
        manager.read_secret.side_effect = error
        with pytest.raises(PaymentVaultError, match="secrets backend unavailable"):
            vault.delete_card()
    else:
        manager.list_secrets.side_effect = error
        with pytest.raises(PaymentVaultError, match="secrets backend unavailable"):
            vault.list_cards()


def test_list_cards_skips_index_empty_and_duplicate_labels() -> None:
    vault, manager = _vault()
    manager.list_secrets.return_value = [
        "gludd/payment/cards/",
        "gludd/payment/cards/index",
        "gludd/payment/cards/work/item",
        "gludd/payment/cards/work/other",
    ]
    manager.read_secret.return_value = None

    assert vault.list_cards() == []
    assert manager.read_secret.call_count == 1
