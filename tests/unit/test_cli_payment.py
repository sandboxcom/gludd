"""Tests for the programmatic payment command tree (``cli_payment.py``).

Covers parser registration/wiring plus each subcommand's behavior against a
FakeVault (no real OpenBao/SecretsManager involved). ``_open_vault`` imports
``SecretsManager`` and ``SecurePaymentVault`` *inside* the function body, so
those two names are monkeypatched at their defining modules
(``general_ludd.secrets.manager`` / ``general_ludd.secrets.payment_vault``)
rather than on ``general_ludd.cli_payment`` itself.
"""
from __future__ import annotations

import argparse
import re
from typing import Any

import pytest

import general_ludd.cli_payment as cli_payment
import general_ludd.secrets.manager as secrets_manager_mod
import general_ludd.secrets.payment_vault as payment_vault_mod
from general_ludd.cli import build_parser
from general_ludd.secrets.payment_vault import PaymentVaultError, redact_card_number

_VISA = "4111111111111111"


def _run(args: list[str]) -> int:
    """Invoke the retained programmatic command tree and capture its exit code.

    Payment handling is intentionally absent from the public ``gludd`` CLI and
    reached through prompting.  The parser/handlers remain available for
    programmatic integrations and are exercised here without weakening that
    public-interface boundary.
    """
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    cli_payment.register(subparsers)
    try:
        parsed = parser.parse_args(["payment", *args])
        handler = getattr(parsed, "func", None)
        if handler is None:
            parser.error("a payment subcommand is required")
        handler(parsed)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0
    return 0


class FakeSecretsManager:
    """Stand-in for SecretsManager; connect() is a no-op unless told to fail."""

    def __init__(self, fail_connect: bool = False) -> None:
        self._fail_connect = fail_connect

    def connect(self) -> None:
        if self._fail_connect:
            raise ConnectionError("no route to OpenBao")


class FakeVault:
    """Stand-in for SecurePaymentVault that records every call it receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.cards: dict[str, dict[str, Any]] = {}
        self.store_error: Exception | None = None
        self.list_error: Exception | None = None
        self.get_meta_error: Exception | None = None
        self.get_last4_error: Exception | None = None
        self.delete_error: Exception | None = None
        # Sentinel object (not None) means "derive from stored card"; an
        # explicit None means "force get_card_last4 to report unknown".
        self._last4_override: object = _UNSET

    def store_card(self, **kwargs: Any) -> str:
        self.calls.append(("store_card", (), dict(kwargs)))
        if self.store_error is not None:
            raise self.store_error
        label = kwargs["label"]
        token = "tok_fake0000"
        self.cards[label] = {
            "token": token,
            "last4": kwargs["card_number"][-4:],
            "brand": "visa",
            "expiry_month": kwargs["expiry_month"],
            "expiry_year": kwargs["expiry_year"],
            "holder_name": kwargs["holder_name"],
            "processor": kwargs["processor"],
            "processor_token": "proc_fake0000",
            "stored_at": "2026-01-01T00:00:00+00:00",
        }
        return token

    def get_card_last4(self, label: str) -> str | None:
        self.calls.append(("get_card_last4", (label,), {}))
        if self.get_last4_error is not None:
            raise self.get_last4_error
        if self._last4_override is not _UNSET:
            return self._last4_override  # type: ignore[return-value]
        meta = self.cards.get(label)
        return meta["last4"] if meta else None

    def get_card_metadata(self, label: str) -> dict[str, Any] | None:
        self.calls.append(("get_card_metadata", (label,), {}))
        if self.get_meta_error is not None:
            raise self.get_meta_error
        return dict(self.cards[label]) if label in self.cards else None

    def list_cards(self) -> list[dict[str, Any]]:
        self.calls.append(("list_cards", (), {}))
        if self.list_error is not None:
            raise self.list_error
        out = []
        for label, meta in self.cards.items():
            m = dict(meta)
            m["label"] = label
            out.append(m)
        return out

    def delete_card(self, label: str) -> bool:
        self.calls.append(("delete_card", (label,), {}))
        if self.delete_error is not None:
            raise self.delete_error
        if label in self.cards:
            del self.cards[label]
            return True
        return False

    def force_unknown_last4(self) -> None:
        self._last4_override = None

    def call_names(self) -> list[str]:
        return [c[0] for c in self.calls]


_UNSET = object()


def _install_vault(monkeypatch: pytest.MonkeyPatch) -> FakeVault:
    """Patch SecretsManager + SecurePaymentVault so _open_vault() returns a FakeVault."""
    vault = FakeVault()
    monkeypatch.setattr(secrets_manager_mod, "SecretsManager", lambda *a, **kw: FakeSecretsManager())
    monkeypatch.setattr(payment_vault_mod, "SecurePaymentVault", lambda sm: vault)
    return vault


def _install_poison_vault(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Patch SecretsManager/SecurePaymentVault to blow up if ever constructed.

    Used to prove argparse-level failures (missing required args) exit before
    _open_vault ever runs.
    """
    hits: list[int] = []

    class _Poison:
        def __init__(self, *a: Any, **kw: Any) -> None:
            hits.append(1)
            raise AssertionError("vault must not be constructed")

    monkeypatch.setattr(secrets_manager_mod, "SecretsManager", _Poison)
    monkeypatch.setattr(payment_vault_mod, "SecurePaymentVault", _Poison)
    return hits


def _seed_card(vault: FakeVault, label: str = "default", last4: str = "1111") -> None:
    vault.cards[label] = {
        "token": "tok_seeded000",
        "last4": last4,
        "brand": "visa",
        "expiry_month": "12",
        "expiry_year": "30",
        "holder_name": "Ada Lovelace",
        "processor": "stripe",
        "processor_token": "proc_seeded000",
        "stored_at": "2026-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Parser registration + subcommand wiring
# ---------------------------------------------------------------------------


def _payment_subactions() -> argparse._SubParsersAction[argparse.ArgumentParser]:
    top = argparse.ArgumentParser()
    sub = top.add_subparsers(dest="command")
    payment_parser = cli_payment.register(sub)
    for action in payment_parser._subparsers._group_actions:  # type: ignore[union-attr]
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("payment parser has no nested subparsers action")


class TestRegister:
    def test_command_is_not_exposed_by_public_cli(self) -> None:
        _parser, subcommand_map = build_parser()
        assert "payment" not in subcommand_map

    def test_all_five_subcommands_registered(self) -> None:
        sub_action = _payment_subactions()
        assert set(sub_action.choices) == {"add", "list", "show", "delete", "provision"}

    def test_add_wired_to_handler(self) -> None:
        sub_action = _payment_subactions()
        assert sub_action.choices["add"].get_default("func") is cli_payment._cmd_payment_add

    def test_list_wired_to_handler(self) -> None:
        sub_action = _payment_subactions()
        assert sub_action.choices["list"].get_default("func") is cli_payment._cmd_payment_list

    def test_show_wired_to_handler(self) -> None:
        sub_action = _payment_subactions()
        assert sub_action.choices["show"].get_default("func") is cli_payment._cmd_payment_show

    def test_delete_wired_to_handler(self) -> None:
        sub_action = _payment_subactions()
        assert sub_action.choices["delete"].get_default("func") is cli_payment._cmd_payment_delete

    def test_provision_wired_to_handler(self) -> None:
        sub_action = _payment_subactions()
        assert sub_action.choices["provision"].get_default("func") is cli_payment._cmd_payment_provision

    def test_docstring_does_not_overstate_no_cleartext_flags(self) -> None:
        doc = cli_payment.__doc__ or ""
        assert "--card-number" in doc
        assert "--cvc" in doc
        assert "DISCOURAGED" in doc.upper()

    def test_add_flag_help_warns_about_shell_exposure(self) -> None:
        sub_action = _payment_subactions()
        add_parser = sub_action.choices["add"]
        help_texts = {a.dest: (a.help or "") for a in add_parser._actions}
        assert "discouraged" in help_texts["card_number"].lower()
        assert "discouraged" in help_texts["cvc"].lower()


# ---------------------------------------------------------------------------
# Required-arg failures exit(2) via argparse, before any vault call
# ---------------------------------------------------------------------------


class TestRequiredArgs:
    @pytest.mark.parametrize(
        "missing_flag_args",
        [
            ["add", "--expiry-year", "30", "--holder-name", "Ada", "--cvc", "123", "--card-number", _VISA],
            ["add", "--expiry-month", "12", "--holder-name", "Ada", "--cvc", "123", "--card-number", _VISA],
            ["add", "--expiry-month", "12", "--expiry-year", "30", "--cvc", "123", "--card-number", _VISA],
        ],
    )
    def test_add_missing_required_flag_exits_2_no_vault_call(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], missing_flag_args: list[str]
    ) -> None:
        hits = _install_poison_vault(monkeypatch)
        code = _run(missing_flag_args)
        assert code == 2
        assert hits == []

    def test_show_missing_label_exits_2_no_vault_call(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hits = _install_poison_vault(monkeypatch)
        assert _run(["show"]) == 2
        assert hits == []

    def test_delete_missing_label_exits_2_no_vault_call(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hits = _install_poison_vault(monkeypatch)
        assert _run(["delete"]) == 2
        assert hits == []

    def test_provision_missing_service_exits_2_no_vault_call(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hits = _install_poison_vault(monkeypatch)
        assert _run(["provision"]) == 2
        assert hits == []


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


class TestPaymentAdd:
    def test_store_card_called_with_exact_args(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        code = _run(
            [
                "add",
                "--card-number",
                _VISA,
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--cvc",
                "737",
                "--holder-name",
                "Ada Lovelace",
                "--label",
                "mycard",
                "--processor",
                "stripe",
            ]
        )
        assert code == 0
        assert vault.calls[0] == (
            "store_card",
            (),
            {
                "card_number": _VISA,
                "expiry_month": "12",
                "expiry_year": "30",
                "cvc": "737",
                "holder_name": "Ada Lovelace",
                "label": "mycard",
                "processor": "stripe",
            },
        )

    def test_output_masks_pan_and_cvc_shows_last4_and_label(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _install_vault(monkeypatch)
        code = _run(
            [
                "add",
                "--card-number",
                _VISA,
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--cvc",
                "737",
                "--holder-name",
                "Ada Lovelace",
                "--label",
                "mycard",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert _VISA not in out
        assert "737" not in out
        assert redact_card_number(_VISA) in out
        assert "1111" in out
        assert "mycard" in out

    def test_getpass_fallback_prompts_twice_when_flags_omitted(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _install_vault(monkeypatch)
        prompts: list[str] = []

        def _fake_getpass(prompt: str = "") -> str:
            prompts.append(prompt)
            return _VISA if "card" in prompt.lower() else "737"

        monkeypatch.setattr("getpass.getpass", _fake_getpass)
        code = _run(
            [
                "add",
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--holder-name",
                "Ada Lovelace",
            ]
        )
        assert code == 0
        assert len(prompts) == 2

    def test_eof_on_cvc_prompt_aborts_exit_1_zero_vault_calls(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        call_count = 0

        def _card_eof(prompt: str = "") -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _VISA
            raise EOFError

        monkeypatch.setattr("getpass.getpass", _card_eof)
        code = _run(
            [
                "add",
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--holder-name",
                "Ada Lovelace",
            ]
        )
        assert code == 1
        assert vault.calls == []
        assert "aborted" in capsys.readouterr().err.lower()

    def test_explicit_flags_skip_getpass_entirely(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _install_vault(monkeypatch)

        def _boom(prompt: str = "") -> str:
            raise AssertionError("getpass.getpass must not be called when flags are given")

        monkeypatch.setattr("getpass.getpass", _boom)
        code = _run(
            [
                "add",
                "--card-number",
                _VISA,
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--cvc",
                "737",
                "--holder-name",
                "Ada Lovelace",
            ]
        )
        assert code == 0

    def test_eof_on_card_number_prompt_aborts_exit_1_zero_vault_calls(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)

        def _eof(prompt: str = "") -> str:
            raise EOFError

        monkeypatch.setattr("getpass.getpass", _eof)
        code = _run(
            [
                "add",
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--holder-name",
                "Ada Lovelace",
            ]
        )
        assert code == 1
        assert vault.calls == []
        assert "aborted" in capsys.readouterr().err.lower()

    def test_empty_cvc_flag_is_an_error_not_a_prompt(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)

        def _boom(prompt: str = "") -> str:
            raise AssertionError("getpass.getpass must not be called for explicit empty --cvc")

        monkeypatch.setattr("getpass.getpass", _boom)
        code = _run(
            [
                "add",
                "--card-number",
                _VISA,
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--cvc",
                "",
                "--holder-name",
                "Ada Lovelace",
            ]
        )
        assert code == 1
        assert vault.calls == []
        err = capsys.readouterr().err
        assert "cvc" in err.lower()
        assert "empty" in err.lower()

    def test_empty_card_number_flag_is_an_error_not_a_prompt(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)

        def _boom(prompt: str = "") -> str:
            raise AssertionError("getpass.getpass must not be called for explicit empty --card-number")

        monkeypatch.setattr("getpass.getpass", _boom)
        code = _run(
            [
                "add",
                "--card-number",
                "",
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--cvc",
                "737",
                "--holder-name",
                "Ada Lovelace",
            ]
        )
        assert code == 1
        assert vault.calls == []
        err = capsys.readouterr().err
        assert "card-number" in err.lower()
        assert "empty" in err.lower()

    def test_value_error_from_store_card_exits_1_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        vault.store_error = ValueError("card_number failed Luhn checksum validation")
        code = _run(
            [
                "add",
                "--card-number",
                "4111111111111112",
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--cvc",
                "737",
                "--holder-name",
                "Ada Lovelace",
            ]
        )
        assert code == 1
        err = capsys.readouterr().err
        assert "Luhn" in err
        assert "Traceback" not in err

    def test_payment_vault_error_from_store_card_exits_1_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        vault.store_error = PaymentVaultError("secrets backend unavailable: boom")
        code = _run(
            [
                "add",
                "--card-number",
                _VISA,
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--cvc",
                "737",
                "--holder-name",
                "Ada Lovelace",
            ]
        )
        assert code == 1
        err = capsys.readouterr().err
        assert "boom" in err
        assert "Traceback" not in err

    def test_post_store_readback_failure_warns_but_exits_0_and_does_not_restore(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        vault.get_last4_error = PaymentVaultError("read failed after write")
        code = _run(
            [
                "add",
                "--card-number",
                _VISA,
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--cvc",
                "737",
                "--holder-name",
                "Ada Lovelace",
                "--label",
                "mycard",
            ]
        )
        assert code == 0
        err = capsys.readouterr().err
        assert "mycard" in err
        assert "read-back failed" in err.lower() or "read back" in err.lower()
        # store_card must be called exactly once — the failed read-back must
        # NOT trigger a re-store.
        assert vault.call_names().count("store_card") == 1

    def test_unknown_last4_renders_question_marks_not_zeroes(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        vault.force_unknown_last4()
        code = _run(
            [
                "add",
                "--card-number",
                _VISA,
                "--expiry-month",
                "12",
                "--expiry-year",
                "30",
                "--cvc",
                "737",
                "--holder-name",
                "Ada Lovelace",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "????" in out
        assert "0000" not in out


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestPaymentList:
    def test_empty_vault_message(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _install_vault(monkeypatch)
        code = _run(["list"])
        out = capsys.readouterr().out
        assert code == 0
        assert "No cards stored." in out

    def test_list_calls_list_cards_exactly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="a", last4="1111")
        code = _run(["list"])
        assert code == 0
        assert vault.calls == [("list_cards", (), {})]

    def test_list_never_prints_a_full_pan(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="a", last4="4242")
        _seed_card(vault, label="b", last4="9999")
        code = _run(["list"])
        out = capsys.readouterr().out
        assert code == 0
        assert re.search(r"\d{12,}", out) is None
        assert "4242" in out
        assert "9999" in out

    def test_backend_error_exits_1_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        vault.list_error = PaymentVaultError("secrets backend unavailable: down")
        code = _run(["list"])
        assert code == 1
        err = capsys.readouterr().err
        assert "down" in err
        assert "Traceback" not in err


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestPaymentShow:
    def test_masked_metadata_fields_present(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard", last4="4242")
        code = _run(["show", "mycard"])
        out = capsys.readouterr().out
        assert code == 0
        for field in ("token", "last4", "brand", "expiry", "holder_name", "processor", "processor_token", "stored_at"):
            assert field in out

    def test_show_calls_get_card_metadata_with_label(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard")
        code = _run(["show", "mycard"])
        assert code == 0
        assert vault.calls == [("get_card_metadata", ("mycard",), {})]

    def test_show_output_has_no_12_digit_run(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard", last4="4242")
        code = _run(["show", "mycard"])
        out = capsys.readouterr().out
        assert code == 0
        assert re.search(r"\d{12,}", out) is None

    def test_not_found_exits_1(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _install_vault(monkeypatch)
        code = _run(["show", "nope"])
        err = capsys.readouterr().err
        assert code == 1
        assert "nope" in err

    def test_backend_error_exits_1_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        vault.get_meta_error = PaymentVaultError("secrets backend unavailable: down")
        code = _run(["show", "mycard"])
        assert code == 1
        err = capsys.readouterr().err
        assert "down" in err
        assert "Traceback" not in err


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestPaymentDelete:
    def test_yes_flag_skips_confirmation_prompt(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard")

        def _boom(prompt: str = "") -> str:
            raise AssertionError("input() must not be called with -y")

        monkeypatch.setattr("builtins.input", _boom)
        code = _run(["delete", "mycard", "-y"])
        assert code == 0
        assert "mycard" in capsys.readouterr().out

    def test_confirmation_no_aborts_without_deleting(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard")
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        code = _run(["delete", "mycard"])
        assert code == 0
        assert "delete_card" not in vault.call_names()
        assert "mycard" in vault.cards

    def test_confirmation_yes_deletes(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard")
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        code = _run(["delete", "mycard"])
        assert code == 0
        assert "delete_card" in vault.call_names()
        assert "mycard" not in vault.cards

    def test_eof_at_confirmation_aborts_exit_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard")

        def _eof(prompt: str = "") -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        code = _run(["delete", "mycard"])
        assert code == 1
        assert "delete_card" not in vault.call_names()
        assert "aborted" in capsys.readouterr().err.lower()

    def test_not_found_exits_1(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _install_vault(monkeypatch)
        code = _run(["delete", "nope", "-y"])
        err = capsys.readouterr().err
        assert code == 1
        assert "nope" in err

    def test_backend_error_exits_1_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard")
        vault.delete_error = PaymentVaultError("secrets backend unavailable: down")
        code = _run(["delete", "mycard", "-y"])
        assert code == 1
        err = capsys.readouterr().err
        assert "down" in err
        assert "Traceback" not in err


# ---------------------------------------------------------------------------
# provision
# ---------------------------------------------------------------------------


class TestPaymentProvision:
    def test_masked_last4_provision_message(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard", last4="4242")
        code = _run(["provision", "netflix", "--label", "mycard"])
        out = capsys.readouterr().out
        assert code == 0
        assert "**** **** **** 4242" in out
        assert "netflix" in out
        assert "stripe" in out

    def test_provision_calls_get_card_metadata_with_label(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        _seed_card(vault, label="mycard")
        code = _run(["provision", "netflix", "--label", "mycard"])
        assert code == 0
        assert vault.calls == [("get_card_metadata", ("mycard",), {})]

    def test_not_found_exits_1(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _install_vault(monkeypatch)
        code = _run(["provision", "netflix", "--label", "nope"])
        err = capsys.readouterr().err
        assert code == 1
        assert "nope" in err

    def test_backend_error_exits_1_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = _install_vault(monkeypatch)
        vault.get_meta_error = PaymentVaultError("secrets backend unavailable: down")
        code = _run(["provision", "netflix", "--label", "mycard"])
        assert code == 1
        err = capsys.readouterr().err
        assert "down" in err
        assert "Traceback" not in err


# ---------------------------------------------------------------------------
# _open_vault
# ---------------------------------------------------------------------------


class TestOpenVault:
    def test_connect_failure_exits_1_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            secrets_manager_mod, "SecretsManager", lambda *a, **kw: FakeSecretsManager(fail_connect=True)
        )
        monkeypatch.setattr(payment_vault_mod, "SecurePaymentVault", lambda sm: FakeVault())
        code = _run(["list"])
        err = capsys.readouterr().err
        assert code == 1
        assert "OpenBao not available" in err
        assert "Traceback" not in err

    def test_ctor_failure_exits_1_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(secrets_manager_mod, "SecretsManager", lambda *a, **kw: FakeSecretsManager())

        def _raise_vault_error(sm: Any) -> Any:
            raise PaymentVaultError("the 'cryptography' library is required")

        monkeypatch.setattr(payment_vault_mod, "SecurePaymentVault", _raise_vault_error)
        code = _run(["list"])
        err = capsys.readouterr().err
        assert code == 1
        assert "Payment vault unavailable" in err
        assert "Traceback" not in err
