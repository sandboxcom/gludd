"""Account backup, deletion, and cloud-service retention notices."""

from __future__ import annotations

from general_ludd.account.backup import (
    backup_account,
    delete_account,
    get_deletion_policy,
)
from general_ludd.account.deletion_notice import (
    SUPPORTED_SERVICES,
    build_deletion_notice,
    get_all_notices,
)
from general_ludd.account.ephemeral import (
    SUPPORTED_PROVIDERS,
    AccountCredentials,
    CliProviderBackend,
    EphemeralAccountManager,
    ProviderBackend,
)
from general_ludd.account.lifecycle_policy import (
    LifecycleAction,
    PolicyConfig,
    evaluate_lifecycle,
)

__all__ = [
    "SUPPORTED_PROVIDERS",
    "SUPPORTED_SERVICES",
    "AccountCredentials",
    "CliProviderBackend",
    "EphemeralAccountManager",
    "LifecycleAction",
    "PolicyConfig",
    "ProviderBackend",
    "backup_account",
    "build_deletion_notice",
    "delete_account",
    "evaluate_lifecycle",
    "get_all_notices",
    "get_deletion_policy",
]
