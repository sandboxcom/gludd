from __future__ import annotations

import datetime
import unittest
from typing import Any

from general_ludd.secrets.payment_vault import (
    PaymentVaultError,
    SecurePaymentVault,
    redact_card_number,
)


class _FakeSecretsManager:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.deletes: list[str] = []
        self.fail: bool = False

    def write_secret(self, path: str, value: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("backend unavailable")
        snapshot = {k: v for k, v in value.items()}
        self.store[path] = snapshot
        self.writes.append((path, snapshot))

    def read_secret(self, path: str) -> dict[str, Any] | None:
        if self.fail:
            raise RuntimeError("backend unavailable")
        v = self.store.get(path)
        return {k: v2 for k, v2 in v.items()} if v is not None else None

    def delete_secret(self, path: str) -> None:
        if self.fail:
            raise RuntimeError("backend unavailable")
        self.deletes.append(path)
        self.store.pop(path, None)

    def list_secrets(self, prefix: str) -> list[str]:
        if self.fail:
            raise RuntimeError("backend unavailable")
        return sorted(k for k in self.store if k.startswith(prefix))


def _make_vault() -> tuple[SecurePaymentVault, _FakeSecretsManager]:
    sm = _FakeSecretsManager()
    return SecurePaymentVault(sm), sm


_VISA = "4111111111111111"
_MC = "5555555555554444"
_AMEX = "378282246310005"


class PaymentVaultTests(unittest.TestCase):
    def test_store_and_retrieve_last4(self) -> None:
        vault, _ = _make_vault()
        vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada Lovelace",
        )
        self.assertEqual(vault.get_card_last4(), "1111")

    def test_store_returns_opaque_token(self) -> None:
        vault, _ = _make_vault()
        token = vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
        )
        self.assertTrue(token.startswith("tok_"))
        self.assertNotEqual(token, _VISA)
        self.assertEqual(len(token), len("tok_") + 16)
        self.assertNotIn(_VISA, token)

    def test_invalid_card_luhn_rejected(self) -> None:
        vault, _ = _make_vault()
        with self.assertRaises(ValueError):
            vault.store_card(
                card_number="4111111111111112",
                expiry_month="12",
                expiry_year="30",
                cvc="123",
                holder_name="Ada",
            )

    def test_card_number_never_in_secret_payload(self) -> None:
        vault, sm = _make_vault()
        vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
        )
        for path, payload in sm.writes:
            self.assertNotIn("card_number", payload, msg=f"path={path}")
            self.assertNotIn("cvc", payload, msg=f"path={path}")
            for key, val in payload.items():
                self.assertNotIn(
                    _VISA,
                    str(val),
                    msg=f"PAN leaked via field {key!r} at {path}",
                )

    def test_card_number_never_logged(self) -> None:
        vault, _ = _make_vault()
        with self.assertLogs(
            "general_ludd.secrets.payment_vault", level="DEBUG"
        ) as ctx:
            vault.store_card(
                card_number=_VISA,
                expiry_month="12",
                expiry_year="30",
                cvc="123",
                holder_name="Ada",
            )
        joined = "\n".join(ctx.output)
        self.assertNotIn(_VISA, joined)
        self.assertIn("1111", joined)

    def test_brand_detection_visa_mastercard_amex(self) -> None:
        vault, _ = _make_vault()
        for num, expected in (
            (_VISA, "visa"),
            (_MC, "mastercard"),
            (_AMEX, "amex"),
        ):
            vault.store_card(
                card_number=num,
                expiry_month="12",
                expiry_year="30",
                cvc="123",
                holder_name="Ada",
                label=f"card-{expected}",
            )
            meta = vault.get_card_metadata(label=f"card-{expected}")
            assert meta is not None
            self.assertEqual(meta["brand"], expected)

    def test_delete_card(self) -> None:
        vault, _ = _make_vault()
        vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
        )
        self.assertTrue(vault.delete_card())
        self.assertIsNone(vault.get_card_last4())
        self.assertFalse(vault.delete_card())

    def test_list_cards_returns_masked_only(self) -> None:
        vault, _ = _make_vault()
        vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
            label="default",
        )
        vault.store_card(
            card_number=_MC,
            expiry_month="11",
            expiry_year="29",
            cvc="456",
            holder_name="Grace",
            label="work",
        )
        cards = vault.list_cards()
        self.assertEqual(len(cards), 2)
        for entry in cards:
            self.assertIn("last4", entry)
            for _key, val in entry.items():
                self.assertNotIn(_VISA, str(val))
                self.assertNotIn(_MC, str(val))
        last4s = {e["last4"] for e in cards}
        self.assertEqual(last4s, {"1111", "4444"})

    def test_envelope_encryption_used(self) -> None:
        vault, sm = _make_vault()
        vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
        )
        card_payload = next(
            p for path, p in sm.writes if path == "gludd/payment/cards/default"
        )
        for field in ("ciphertext", "nonce", "encrypted_dek"):
            self.assertIn(field, card_payload)
        self.assertNotIn(_VISA, card_payload["ciphertext"])
        self.assertNotIn(_VISA, card_payload["nonce"])
        self.assertNotIn(_VISA, card_payload["encrypted_dek"])

    def test_kek_auto_generated_on_first_use(self) -> None:
        vault, sm = _make_vault()
        vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
        )
        kek_writes = [p for p, _ in sm.writes if p == "gludd/payment/kek"]
        self.assertEqual(len(kek_writes), 1)
        vault.store_card(
            card_number=_MC,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
            label="work",
        )
        kek_writes = [p for p, _ in sm.writes if p == "gludd/payment/kek"]
        self.assertEqual(len(kek_writes), 1)

    def test_get_processor_token(self) -> None:
        vault, _ = _make_vault()
        vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
            processor="stripe",
        )
        tok = vault.get_processor_token()
        assert tok is not None
        self.assertTrue(tok.startswith("proc_stripe_"))
        suffix = tok[len("proc_stripe_"):]
        self.assertEqual(len(suffix), 16)
        int(suffix, 16)

    def test_expiry_validation_rejects_past_year(self) -> None:
        vault, _ = _make_vault()
        with self.assertRaises(ValueError):
            vault.store_card(
                card_number=_VISA,
                expiry_month="13",
                expiry_year="30",
                cvc="123",
                holder_name="Ada",
            )
        current_mod = datetime.date.today().year % 100
        if current_mod > 0:
            past = (current_mod - 1) % 100
            past_str = f"{past:02d}"
            with self.assertRaises(ValueError):
                vault.store_card(
                    card_number=_VISA,
                    expiry_month="12",
                    expiry_year=past_str,
                    cvc="123",
                    holder_name="Ada",
                )

    def test_get_card_metadata_excludes_sensitive(self) -> None:
        vault, _ = _make_vault()
        vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
        )
        meta = vault.get_card_metadata()
        assert meta is not None
        self.assertNotIn("card_number", meta)
        self.assertNotIn("cvc", meta)
        self.assertNotIn("ciphertext", meta)
        self.assertNotIn("nonce", meta)
        self.assertNotIn("encrypted_dek", meta)
        self.assertEqual(meta["last4"], "1111")
        self.assertEqual(meta["brand"], "visa")
        self.assertEqual(meta["processor"], "stripe")

    def test_storage_path_uses_label(self) -> None:
        vault, sm = _make_vault()
        vault.store_card(
            card_number=_VISA,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
            label="default",
        )
        vault.store_card(
            card_number=_MC,
            expiry_month="12",
            expiry_year="30",
            cvc="123",
            holder_name="Ada",
            label="work",
        )
        paths = {p for p, _ in sm.writes if p.startswith("gludd/payment/cards/")}
        self.assertIn("gludd/payment/cards/default", paths)
        self.assertIn("gludd/payment/cards/work", paths)

    def test_backend_errors_wrapped(self) -> None:
        vault, sm = _make_vault()
        sm.fail = True
        with self.assertRaises(PaymentVaultError):
            vault.store_card(
                card_number=_VISA,
                expiry_month="12",
                expiry_year="30",
                cvc="123",
                holder_name="Ada",
            )

    def test_redact_card_number_helper(self) -> None:
        self.assertEqual(redact_card_number(_VISA), "**** **** **** 1111")
        self.assertEqual(redact_card_number("4111-1111-1111-1111"), "**** **** **** 1111")


if __name__ == "__main__":
    unittest.main()
