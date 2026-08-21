"""Provide linearizable OIDC token acquisition for Hugging Face access."""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from general_ludd.small_models.oidc import acquire_oidc_token

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_OIDC_BUFFER_SEC = 60
_DEFAULT_TTL_SEC = 3600


@dataclass
class OidcToken:
    """Represent one acquired OIDC token and its expiry metadata."""

    token: str
    expires_at: float
    provider: str
    acquired_at: float = 0.0

    def __post_init__(self) -> None:
        """Record the acquisition time when the provider did not supply one."""
        if self.acquired_at == 0.0:
            self.acquired_at = time.time()

    @property
    def is_expired(self) -> bool:
        """Return whether the token is within the refresh safety window."""
        return time.time() >= (self.expires_at - _OIDC_BUFFER_SEC)

    @property
    def remaining_seconds(self) -> float:
        """Return the token's non-negative remaining lifetime in seconds."""
        return max(0.0, self.expires_at - time.time())


class HfOidcAuth:
    """HuggingFace OIDC authentication — acquires and caches tokens from OIDC providers.

    Supports cloud-native identity (AWS, GCP, Azure) and custom OIDC endpoints.
    Tokens are automatically refreshed before expiry with a 60-second buffer.
    """

    def __init__(
        self,
        provider: str | None = None,
        endpoint: str | None = None,
        client_id: str | None = None,
        token_ttl: float = _DEFAULT_TTL_SEC,
    ) -> None:
        """Configure the provider and initialize the instance-local cache lock."""
        self.provider = provider or os.environ.get("HF_OIDC_PROVIDER", "")
        self.endpoint = endpoint or os.environ.get("HF_OIDC_ENDPOINT", "")
        self.client_id = client_id or os.environ.get("HF_OIDC_CLIENT_ID", "")
        self.token_ttl = token_ttl
        self._cached: OidcToken | None = None
        self._cache_lock = threading.RLock()

    def get_token(self) -> str | None:
        """Return a valid token, refreshing via OIDC if needed."""
        with self._cache_lock:
            if self._cached is not None and not self._cached.is_expired:
                return self._cached.token

            new_token = self._acquire()
            if new_token is not None:
                return new_token.token

            if self._cached is not None and self._cached.remaining_seconds > 0:
                logger.warning(
                    "OIDC refresh failed; returning stale token (%.0fs remaining).", self._cached.remaining_seconds
                )
                return self._cached.token
            return None

    def refresh(self) -> str | None:
        """Invalidate and reacquire the cached token as one locked transaction."""
        with self._cache_lock:
            self._cached = None
            return self.get_token()

    def invalidate(self) -> None:
        """Invalidate the cached token after any in-flight acquisition completes."""
        with self._cache_lock:
            self._cached = None

    def has_valid_token(self) -> bool:
        """Return whether this instance currently owns a non-expired token."""
        with self._cache_lock:
            return self._cached is not None and not self._cached.is_expired

    def _acquire(self) -> OidcToken | None:
        provider = self.provider
        token_str: str | None = None

        if provider:
            token_str = acquire_oidc_token(
                provider=provider,
                client_id=self.client_id or None,
            )
        elif self.endpoint:
            token_str = self._fetch_from_endpoint(self.endpoint)
        else:
            logger.debug("No OIDC provider or endpoint configured; token acquisition skipped.")
            return None

        if not token_str:
            logger.warning("OIDC token acquisition returned empty token.")
            return None

        expires_at = self._extract_expiry(token_str)
        token = OidcToken(
            token=token_str,
            expires_at=expires_at,
            provider=provider or "custom_endpoint",
        )
        self._cached = token
        logger.info("Acquired OIDC token from %s (expires in %.0fs).", token.provider, token.remaining_seconds)
        return token

    def _fetch_from_endpoint(self, endpoint: str) -> str | None:
        import urllib.request

        try:
            req = urllib.request.Request(endpoint)
            req.add_header("Accept", "application/json")
            if self.client_id:
                req.add_header("X-Client-ID", self.client_id)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                token = data.get("token") or data.get("access_token") or data.get("id_token")
                return token if isinstance(token, str) else None
        except Exception:
            logger.exception("Failed to fetch OIDC token from endpoint %s", endpoint)
            return None

    def _extract_expiry(self, token: str) -> float:
        try:
            parts = token.split(".")
            if len(parts) == 3:
                padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(padded).decode())
                if "exp" in payload and isinstance(payload["exp"], (int, float)):
                    return float(payload["exp"])
        except Exception:
            pass
        return time.time() + self.token_ttl
