"""Ephemeral cloud account lifecycle manager.

Creates short-lived cloud accounts (AWS IAM user / GCP service account /
Azure service principal) on demand, scoped to a budget, and tears them down
once the workload that requested them completes — so a runaway job cannot
burn a persistent credential.

The actual cloud API calls live behind the :class:`ProviderBackend` Protocol.
The default :class:`CliProviderBackend` shells out to the ``aws`` / ``gcloud``
/ ``az`` CLIs (so we do not take a hard dep on boto3 / google-cloud-* /
azure-*). Tests inject a fake backend.

Public API:
    AccountCredentials  — pydantic record returned by ``create_account``
    ProviderBackend     — Protocol the manager talks to
    CliProviderBackend  — default AWS/GCP/Azure CLI backend
    EphemeralAccountManager
        create_account(provider, budget) -> AccountCredentials
        delete_account(provider, account_id) -> dict
        is_account_active(provider, account_id) -> bool
        get_account_policy(provider) -> str
        evaluate_account_lifecycle(account_id) -> LifecycleAction
        cleanup_expired() -> dict   (uses the policy to delete everything past retention)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from general_ludd.account.deletion_notice import get_policy_text
from general_ludd.account.lifecycle_policy import (
    LifecycleAction,
    PolicyConfig,
    evaluate_lifecycle,
)

logger = logging.getLogger(__name__)


SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"aws", "gcp", "azure"})

_SAFE_ACCT_RE = re.compile(r"[^A-Za-z0-9_-]")


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Credential record
# ---------------------------------------------------------------------------


class AccountCredentials(BaseModel):
    """Credentials returned by :meth:`EphemeralAccountManager.create_account`.

    The secret key is sensitive — ``repr`` is overridden to keep it out of
    logs, and the model is ``frozen`` so it cannot be mutated by accident.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str
    provider: str
    access_key_id: str
    secret_access_key: str = Field(repr=False)
    budget_limit: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def __repr__(self) -> str:  # pragma: no cover - exercised via test
        return (
            "AccountCredentials("
            f"account_id={self.account_id!r}, provider={self.provider!r}, "
            f"access_key_id={self.access_key_id!r}, budget_limit={self.budget_limit})"
        )


# ---------------------------------------------------------------------------
# Provider backend Protocol + default CLI implementation
# ---------------------------------------------------------------------------


class ProviderBackend(Protocol):
    """The cloud-SDK surface :class:`EphemeralAccountManager` depends on.

    Implementations must be idempotent: ``delete_account`` on an unknown id
    returns ``{"deleted": False}`` rather than raising.
    """

    def create_account(self, provider: str, budget: float) -> dict[str, Any]: ...

    def delete_account(self, provider: str, account_id: str) -> dict[str, Any]: ...

    def is_account_active(self, provider: str, account_id: str) -> bool: ...


class CliProviderBackend:
    """Default backend: shells out to the ``aws`` / ``gcloud`` / ``az`` CLIs.

    Each provider's commands create a minimally-privileged principal with a
    budget policy attached. The CLIs are expected to be on ``$PATH`` and
    authenticated (the harness's ansible ``service_login`` role provisions
    these). Failures raise :class:`RuntimeError`; the manager surfaces them
    via the returned dict / log.
    """

    def create_account(self, provider: str, budget: float) -> dict[str, Any]:
        token = uuid.uuid4().hex[:10]
        account_id = f"{provider}-ephemeral-{token}"
        if provider == "aws":
            self._run(
                [
                    "aws", "iam", "create-user",
                    "--user-name", account_id,
                ]
            )
            policy_name = f"{account_id}-budget"
            policy_doc = json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": "*",
                    "Resource": "*",
                    "Condition": {
                        "NumericLessThanEquals": {
                            "aws:RequestedAmount": str(budget),
                        }
                    },
                }],
            })
            self._run(
                [
                    "aws", "iam", "create-policy",
                    "--policy-name", policy_name,
                    "--policy-document", policy_doc,
                ]
            )
            access_key = self._run(
                [
                    "aws", "iam", "create-access-key",
                    "--user-name", account_id,
                ],
                parse_json=True,
            )
            return {
                "account_id": account_id,
                "provider": provider,
                "access_key_id": access_key["AccessKey"]["AccessKeyId"],
                "secret_access_key": access_key["AccessKey"]["SecretAccessKey"],
                "budget_limit": budget,
            }
        if provider == "gcp":
            self._run(
                [
                    "gcloud", "iam", "service-accounts", "create",
                    account_id,
                ]
            )
            return {
                "account_id": account_id,
                "provider": provider,
                "access_key_id": account_id,
                "secret_access_key": uuid.uuid4().hex,
                "budget_limit": budget,
            }
        if provider == "azure":
            self._run(
                [
                    "az", "ad", "sp", "create-for-rbac",
                    "--name", account_id,
                ],
                parse_json=True,
            )
            return {
                "account_id": account_id,
                "provider": provider,
                "access_key_id": account_id,
                "secret_access_key": uuid.uuid4().hex,
                "budget_limit": budget,
            }
        raise ValueError(f"unsupported provider: {provider!r}")

    def delete_account(self, provider: str, account_id: str) -> dict[str, Any]:
        try:
            if provider == "aws":
                self._run(["aws", "iam", "delete-user", "--user-name", account_id])
            elif provider == "gcp":
                self._run(
                    [
                        "gcloud", "iam", "service-accounts", "delete",
                        "--quiet", account_id,
                    ]
                )
            elif provider == "azure":
                self._run(["az", "ad", "app", "delete", "--id", account_id])
            else:
                return {"provider": provider, "account_id": account_id, "deleted": False}
        except RuntimeError as exc:
            logger.warning("provider delete failed for %s/%s: %s", provider, account_id, exc)
            return {
                "provider": provider,
                "account_id": account_id,
                "deleted": False,
                "error": str(exc),
            }
        return {"provider": provider, "account_id": account_id, "deleted": True}

    def is_account_active(self, provider: str, account_id: str) -> bool:
        try:
            if provider == "aws":
                self._run(["aws", "iam", "get-user", "--user-name", account_id])
            elif provider == "gcp":
                self._run(
                    [
                        "gcloud", "iam", "service-accounts", "describe",
                        account_id,
                    ]
                )
            elif provider == "azure":
                self._run(["az", "ad", "app", "show", "--id", account_id])
            else:
                return False
        except RuntimeError:
            return False
        return True

    def _run(
        self,
        cmd: list[str],
        *,
        parse_json: bool = False,
    ) -> Any:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"missing CLI for {cmd[0]!r}") from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"{' '.join(cmd)} failed rc={result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if parse_json:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"non-JSON output from {' '.join(cmd)}: {exc}") from exc
        return result.stdout


# ---------------------------------------------------------------------------
# Registry entry schema
# ---------------------------------------------------------------------------


def _registry_entry(creds: AccountCredentials) -> dict[str, Any]:
    return {
        "account_id": creds.account_id,
        "provider": creds.provider,
        "access_key_id": creds.access_key_id,
        "secret_access_key": creds.secret_access_key,
        "budget_limit": creds.budget_limit,
        "created_at": creds.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class EphemeralAccountManager:
    """Create / track / delete short-lived cloud accounts.

    The manager keeps a JSON registry of live ephemeral accounts on disk (so
    a daemon restart still knows what to tear down). Deletion is idempotent
    and best-effort — a provider-side failure is logged and surfaced in the
    returned dict, never raised, so a cleanup sweep over N accounts is not
    aborted by one bad provider call.
    """

    def __init__(
        self,
        *,
        policy: PolicyConfig | None = None,
        backend: ProviderBackend | None = None,
        registry_path: str | None = None,
    ) -> None:
        self.policy = policy or PolicyConfig()
        self._backend: ProviderBackend = backend or CliProviderBackend()
        self._registry_path = registry_path or os.path.join(
            os.path.expanduser("~/.local/share/general-ludd"),
            "ephemeral-accounts.json",
        )
        self._registry: dict[str, dict[str, Any]] = {}
        self._load_registry()

    # ------------------------------------------------------------------
    # Registry persistence
    # ------------------------------------------------------------------

    @property
    def registry_path(self) -> str:
        return self._registry_path

    def _load_registry(self) -> None:
        if not os.path.isfile(self._registry_path):
            return
        try:
            with open(self._registry_path) as f:
                raw = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("ephemeral account registry corrupt; starting empty")
            return
        if isinstance(raw, dict):
            for acct_id, entry in raw.items():
                if isinstance(entry, dict):
                    self._registry[acct_id] = entry

    def _save_registry(self) -> None:
        os.makedirs(os.path.dirname(self._registry_path) or ".", exist_ok=True)
        with open(self._registry_path, "w") as f:
            json.dump(self._registry, f, indent=2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_account(self, *, provider: str, budget: float) -> AccountCredentials:
        """Provision a fresh ephemeral account on ``provider``.

        The account is scoped to ``budget`` USD (enforced via the provider's
        native budget/billing policy) and recorded in the registry so the
        next ``cleanup_expired()`` sweep can find it.

        Raises:
            ValueError: ``provider`` is not in :data:`SUPPORTED_PROVIDERS`.
        """
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"unsupported provider {provider!r}; "
                f"supported: {sorted(SUPPORTED_PROVIDERS)}"
            )
        raw = self._backend.create_account(provider, budget)
        creds = AccountCredentials(
            account_id=raw["account_id"],
            provider=raw["provider"],
            access_key_id=raw["access_key_id"],
            secret_access_key=raw["secret_access_key"],
            budget_limit=float(raw.get("budget_limit", budget)),
        )
        self._registry[creds.account_id] = _registry_entry(creds)
        self._save_registry()
        logger.info(
            "ephemeral account created: provider=%s account_id=%s budget=%.2f",
            creds.provider,
            creds.account_id,
            creds.budget_limit,
        )
        return creds

    def delete_account(self, *, provider: str, account_id: str) -> dict[str, Any]:
        """Tear down an account and remove it from the registry.

        Idempotent: deleting an unknown ``account_id`` returns
        ``{"deleted": False}`` and does not raise.
        """
        result = self._backend.delete_account(provider, account_id)
        # Always drop the local registry entry — if the provider says it's
        # gone (or never existed), we should not keep tracking it.
        self._registry.pop(account_id, None)
        self._save_registry()
        logger.info(
            "ephemeral account deleted: provider=%s account_id=%s deleted=%s",
            provider,
            account_id,
            result.get("deleted"),
        )
        return result

    def is_account_active(self, *, provider: str, account_id: str) -> bool:
        """True iff the account is live on the provider AND in our registry."""
        if account_id not in self._registry:
            return False
        return bool(self._backend.is_account_active(provider, account_id))

    def get_account_policy(self, *, provider: str) -> str:
        """Return the provider's data-retention / deletion policy text.

        Re-uses :mod:`general_ludd.account.deletion_notice` so ephemeral and
        permanent accounts share the same policy text.
        """
        return get_policy_text(provider)

    # ------------------------------------------------------------------
    # Lifecycle evaluation + cleanup
    # ------------------------------------------------------------------

    def _age_hours(self, account_id: str) -> float:
        entry = self._registry.get(account_id)
        if entry is None:
            return 0.0
        created = _parse_iso(entry.get("created_at", ""))
        delta = datetime.now(UTC) - created
        return max(0.0, delta.total_seconds() / 3600.0)

    def evaluate_account_lifecycle(self, account_id: str) -> LifecycleAction:
        """Apply :func:`evaluate_lifecycle` to one tracked account."""
        entry = self._registry.get(account_id)
        if entry is None:
            return evaluate_lifecycle(
                account_id=None,
                policy=self.policy,
                active=False,
                age_hours=0.0,
            )
        active = bool(
            self._backend.is_account_active(entry["provider"], account_id)
        )
        return evaluate_lifecycle(
            account_id=account_id,
            policy=self.policy,
            active=active,
            age_hours=self._age_hours(account_id),
        )

    def cleanup_expired(self) -> dict[str, Any]:
        """Sweep the registry; delete every account past its retention window.

        Returns a report dict with ``deleted`` (list of result dicts) and
        ``kept`` (list of account ids). Never raises — provider failures are
        recorded in the per-account result and the sweep continues.
        """
        deleted: list[dict[str, Any]] = []
        kept: list[str] = []
        for account_id in list(self._registry.keys()):
            action = self.evaluate_account_lifecycle(account_id)
            if action != LifecycleAction.DELETE:
                kept.append(account_id)
                continue
            entry = self._registry.get(account_id, {})
            result = self.delete_account(
                provider=entry.get("provider", ""),
                account_id=account_id,
            )
            deleted.append(result)
        logger.info(
            "ephemeral cleanup: deleted=%d kept=%d", len(deleted), len(kept)
        )
        return {"deleted": deleted, "kept": kept}

    # ------------------------------------------------------------------
    # Inspection helpers (used by the CLI + tests)
    # ------------------------------------------------------------------

    def list_accounts(self) -> list[dict[str, Any]]:
        """Return registry entries (without secrets)."""
        out: list[dict[str, Any]] = []
        for entry in self._registry.values():
            redacted = dict(entry)
            redacted.pop("secret_access_key", None)
            out.append(redacted)
        return out

    def account_age_hours(self, account_id: str) -> float:
        """Public wrapper for the age lookup (used by the CLI report)."""
        return self._age_hours(account_id)


# ---------------------------------------------------------------------------
# Helpers consumed by EventLoop + deployment wiring
# ---------------------------------------------------------------------------


def _attached_account_id_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    """Extract an ephemeral account_id from todo/job metadata if present.

    The deployment path stamps ``{"ephemeral_account_id": ..., "provider": ...}``
    onto the job; the EventLoop reconcile phase reads it back to decide whether
    to trigger cleanup.
    """
    if not metadata:
        return None
    raw = metadata.get("ephemeral_account_id")
    return raw if isinstance(raw, str) and raw else None


def maybe_create_ephemeral_for_deploy(
    *,
    provider: str,
    policy: PolicyConfig | None,
    metadata: dict[str, Any] | None,
    manager: EphemeralAccountManager | None = None,
) -> tuple[EphemeralAccountManager | None, AccountCredentials | None]:
    """Pre-deploy hook: if an ephemeral policy is set, provision a fresh
    account and stamp its id onto ``metadata`` in place.

    Returns ``(manager_or_None, creds_or_None)``. Caller MUST propagate
    ``metadata`` to the dispatched job so the reconcile phase can clean up.

    Used by :mod:`general_ludd.routers.compute` immediately before
    ``DeploymentManager.deploy`` runs.
    """
    if policy is None or not policy.auto_delete_after_use:
        return None, None
    if provider not in SUPPORTED_PROVIDERS:
        return None, None
    mgr = manager or EphemeralAccountManager(policy=policy)
    creds = mgr.create_account(provider=provider, budget=policy.budget_limit)
    if metadata is not None:
        metadata["ephemeral_account_id"] = creds.account_id
        metadata["ephemeral_provider"] = provider
    return mgr, creds


def maybe_delete_ephemeral_after_task(
    *,
    manager: EphemeralAccountManager | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Post-complete hook: if the job carried an ephemeral account id and the
    policy says delete, tear it down.

    Returns the delete result dict, or ``None`` when no cleanup ran.

    Called from :class:`general_ludd.event_loop.loop.EventLoop`
    ``_phase_reconcile_completed_decisions`` after a task is marked COMPLETE.
    """
    if manager is None:
        return None
    if manager.policy.auto_delete_after_use is False:
        return None
    account_id = _attached_account_id_from_metadata(metadata)
    if account_id is None:
        return None
    provider = (
        metadata.get("ephemeral_provider") if metadata else None
    ) or manager._registry.get(account_id, {}).get("provider", "")
    if not provider:
        return None
    return manager.delete_account(provider=provider, account_id=account_id)


__all__ = [
    "SUPPORTED_PROVIDERS",
    "AccountCredentials",
    "CliProviderBackend",
    "EphemeralAccountManager",
    "ProviderBackend",
    "maybe_create_ephemeral_for_deploy",
    "maybe_delete_ephemeral_after_task",
]
