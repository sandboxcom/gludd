"""XDG browser-based login flow for gludd services (OAuth2 + PKCE)."""

from general_ludd.auth.browser_login import (
    SERVICE_PRESETS,
    BrowserLoginFlow,
    CredentialStore,
    EnvCredentialStore,
    ServiceConfig,
    list_services,
    login,
)

__all__ = [
    "SERVICE_PRESETS",
    "BrowserLoginFlow",
    "CredentialStore",
    "EnvCredentialStore",
    "ServiceConfig",
    "list_services",
    "login",
]
