"""``gludd payment`` CLI subcommand — PCI-DSS style payment card vault.

Stores card data under envelope encryption in OpenBao (see
``general_ludd.secrets.payment_vault``). By default ``add`` reads the card
number and CVC interactively via ``getpass`` so they never enter shell
history or a process listing. The ``--card-number``/``--cvc`` flags exist for
non-interactive/scripted use but are DISCOURAGED: values passed on the
command line are exposed in shell history and to any local user who can read
the process list (``ps``); prefer the interactive prompt whenever a human is
present. Only masked metadata (last4, brand, expiry, holder) and the opaque
processor token are ever printed.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from typing import Any


def _open_vault() -> Any:
    from general_ludd.secrets.manager import SecretsManager
    from general_ludd.secrets.payment_vault import PaymentVaultError, SecurePaymentVault

    try:
        sm = SecretsManager()
        sm.connect()
    except Exception as exc:
        print(
            f"OpenBao not available: {exc}\n"
            "Start the OpenBao container (or run `make init`) and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        return SecurePaymentVault(sm)
    except PaymentVaultError as exc:
        print(f"Payment vault unavailable: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_payment_add(args: argparse.Namespace) -> None:
    from general_ludd.secrets.payment_vault import PaymentVaultError, redact_card_number

    vault = _open_vault()
    card_number = args.card_number
    if card_number is None:
        try:
            card_number = getpass.getpass("Card number: ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.", file=sys.stderr)
            sys.exit(1)
    elif card_number == "":
        print("Error: --card-number must not be empty", file=sys.stderr)
        sys.exit(1)
    cvc = args.cvc
    if cvc is None:
        try:
            cvc = getpass.getpass("CVC: ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.", file=sys.stderr)
            sys.exit(1)
    elif cvc == "":
        print("Error: --cvc must not be empty", file=sys.stderr)
        sys.exit(1)

    try:
        vault.store_card(
            card_number=card_number,
            expiry_month=args.expiry_month,
            expiry_year=args.expiry_year,
            cvc=cvc,
            holder_name=args.holder_name,
            label=args.label,
            processor=args.processor,
        )
    except (ValueError, PaymentVaultError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        last4 = vault.get_card_last4(args.label)
        meta = vault.get_card_metadata(args.label) or {}
    except PaymentVaultError as exc:
        print(
            f"Card stored under label '{args.label}', but confirmation "
            f"read-back failed: {exc}",
            file=sys.stderr,
        )
        return

    brand = meta.get("brand", "unknown")
    masked = redact_card_number("0" * 12 + last4) if last4 else "**** **** **** ????"
    print(f"Stored card {masked} ({brand}) as label '{args.label}'.")


def _cmd_payment_list(args: argparse.Namespace) -> None:
    from general_ludd.secrets.payment_vault import PaymentVaultError

    vault = _open_vault()
    try:
        cards = vault.list_cards()
    except PaymentVaultError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not cards:
        print("No cards stored.")
        return
    print(f"{'label':<16} {'last4':<6} {'brand':<12} {'expiry':<8} {'holder'}")
    print("-" * 64)
    for meta in cards:
        label = str(meta.get("label", "?"))
        last4 = str(meta.get("last4", "?"))
        brand = str(meta.get("brand", "?"))
        expiry = f"{meta.get('expiry_month', '?')}/{meta.get('expiry_year', '?')}"
        holder = str(meta.get("holder_name", "?"))
        print(f"{label:<16} {last4:<6} {brand:<12} {expiry:<8} {holder}")


def _cmd_payment_show(args: argparse.Namespace) -> None:
    from general_ludd.secrets.payment_vault import PaymentVaultError

    vault = _open_vault()
    try:
        meta = vault.get_card_metadata(args.label)
    except PaymentVaultError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if meta is None:
        print(f"No card stored under label '{args.label}'.", file=sys.stderr)
        sys.exit(1)
    print(f"label:            {args.label}")
    print(f"token:            {meta.get('token', '?')}")
    print(f"last4:            {meta.get('last4', '?')}")
    print(f"brand:            {meta.get('brand', '?')}")
    print(f"expiry:           {meta.get('expiry_month', '?')}/{meta.get('expiry_year', '?')}")
    print(f"holder_name:      {meta.get('holder_name', '?')}")
    print(f"processor:        {meta.get('processor', '?')}")
    print(f"processor_token:  {meta.get('processor_token', '?')}")
    print(f"stored_at:        {meta.get('stored_at', '?')}")


def _cmd_payment_delete(args: argparse.Namespace) -> None:
    vault = _open_vault()
    if not args.yes:
        try:
            confirm = input(f"Delete card '{args.label}'? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.", file=sys.stderr)
            sys.exit(1)
        if confirm.strip().lower() not in {"y", "yes"}:
            print("Aborted.")
            return
    from general_ludd.secrets.payment_vault import PaymentVaultError

    try:
        deleted = vault.delete_card(args.label)
    except PaymentVaultError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not deleted:
        print(f"No card stored under label '{args.label}'.", file=sys.stderr)
        sys.exit(1)
    print(f"Deleted card '{args.label}'.")


def _cmd_payment_provision(args: argparse.Namespace) -> None:
    from general_ludd.secrets.payment_vault import PaymentVaultError

    vault = _open_vault()
    try:
        meta = vault.get_card_metadata(args.label)
    except PaymentVaultError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if meta is None:
        print(f"No card stored under label '{args.label}'.", file=sys.stderr)
        sys.exit(1)
    last4 = meta.get("last4", "?")
    processor = meta.get("processor", "?")
    token = meta.get("processor_token", "?")
    print(
        f"Provisioned {args.service} using card **** **** **** {last4} "
        f"(processor={processor}, token={token})."
    )


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    """Register the `payment` subcommand tree on the given subparsers."""
    payment_parser = subparsers.add_parser(
        "payment",
        help="PCI-DSS payment card vault (envelope-encrypted, stored in OpenBao).",
    )
    payment_parser.set_defaults(func=None)
    payment_sub = payment_parser.add_subparsers(dest="payment_command")

    p = payment_sub.add_parser("add", help="Store a card under a label.")
    p.add_argument(
        "--card-number",
        default=None,
        help="Card number (prompted via getpass if omitted). DISCOURAGED: visible in "
        "shell history and `ps`; prefer the interactive prompt.",
    )
    p.add_argument("--expiry-month", required=True, help="Expiry month (01-12).")
    p.add_argument("--expiry-year", required=True, help="Expiry year (2-digit, e.g. 30).")
    p.add_argument(
        "--cvc",
        default=None,
        help="CVC (prompted via getpass if omitted). DISCOURAGED: visible in shell "
        "history and `ps`; prefer the interactive prompt.",
    )
    p.add_argument("--holder-name", required=True, help="Cardholder name.")
    p.add_argument("--label", default="default", help="Label to store the card under (default: 'default').")
    p.add_argument("--processor", default="stripe", help="Payment processor name (default: 'stripe').")
    p.set_defaults(func=_cmd_payment_add)

    p = payment_sub.add_parser("list", help="List stored cards (masked only).")
    p.set_defaults(func=_cmd_payment_list)

    p = payment_sub.add_parser("show", help="Show masked metadata for one card.")
    p.add_argument("label", help="Card label.")
    p.set_defaults(func=_cmd_payment_show)

    p = payment_sub.add_parser("delete", help="Delete a stored card.")
    p.add_argument("label", help="Card label.")
    p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation.")
    p.set_defaults(func=_cmd_payment_delete)

    p = payment_sub.add_parser(
        "provision",
        help="Simulate 1-click provisioning for a service using a stored card's processor token.",
    )
    p.add_argument("service", help="Service to provision.")
    p.add_argument("--label", default="default", help="Card label to use (default: 'default').")
    p.set_defaults(func=_cmd_payment_provision)

    return payment_parser
