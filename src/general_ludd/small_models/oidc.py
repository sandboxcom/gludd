from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)


def acquire_oidc_token(provider: str, client_id: str | None = None) -> str | None:
    """Acquire an OIDC token from a cloud provider or custom endpoint.

    Supported providers: aws, gcp, azure, env (reads env var directly).
    Returns the raw token string, or None on failure.
    """
    provider = provider.lower().strip()

    if provider == "aws":
        return _fetch_aws_oidc_token()
    if provider == "gcp":
        return _fetch_gcp_oidc_token(client_id)
    if provider == "azure":
        return _fetch_azure_oidc_token(client_id)
    if provider == "env":
        return _fetch_env_oidc_token()
    if provider == "custom":
        return _fetch_custom_oidc_token(client_id)
    logger.warning("Unknown OIDC provider: %s", provider)
    return None


def _fetch_aws_oidc_token() -> str | None:
    """Fetch OIDC token from AWS IAM Roles Anywhere / ECS metadata."""
    relative_uri = os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "")
    if relative_uri:
        try:
            url = f"http://169.254.170.2{relative_uri}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                return data.get("Token") or data.get("AccessKeyId")
        except Exception:
            logger.exception("AWS OIDC: failed to fetch container credentials")
            return None

    web_identity_file = os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE", "")
    if web_identity_file:
        try:
            return open(web_identity_file).read().strip()
        except Exception:
            logger.exception("AWS OIDC: failed to read web identity token file")
            return None

    logger.debug("AWS OIDC: no credential source found")
    return None


def _fetch_gcp_oidc_token(client_id: str | None = None) -> str | None:
    """Fetch OIDC token from GCP metadata server."""
    audience = client_id or "https://huggingface.co"
    try:
        url = f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={audience}&format=full"
        req = urllib.request.Request(url)
        req.add_header("Metadata-Flavor", "Google")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode()
    except Exception:
        logger.exception("GCP OIDC: failed to fetch identity token")
        return None


def _fetch_azure_oidc_token(client_id: str | None = None) -> str | None:
    """Fetch OIDC token from Azure Instance Metadata Service."""
    endpoint = os.environ.get("IDENTITY_ENDPOINT", "")
    identity_header = os.environ.get("IDENTITY_HEADER", "")
    if not endpoint:
        logger.debug("Azure OIDC: IDENTITY_ENDPOINT not set")
        return None
    try:
        resource = client_id or "https://huggingface.co"
        url = f"{endpoint}?resource={resource}&api-version=2019-08-01"
        req = urllib.request.Request(url)
        req.add_header("X-IDENTITY-HEADER", identity_header)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data.get("access_token") or data.get("token")
    except Exception:
        logger.exception("Azure OIDC: failed to fetch identity token")
        return None


def _fetch_env_oidc_token() -> str | None:
    """Read OIDC token from environment variable."""
    return os.environ.get("HF_OIDC_TOKEN") or os.environ.get("OIDC_TOKEN")


def _fetch_custom_oidc_token(client_id: str | None = None) -> str | None:
    """Fetch OIDC token from a custom endpoint specified via env."""
    url = os.environ.get("HF_OIDC_CUSTOM_ENDPOINT", "")
    if not url:
        logger.debug("Custom OIDC: HF_OIDC_CUSTOM_ENDPOINT not set")
        return None
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        if client_id:
            req.add_header("X-Client-ID", client_id)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("token") or data.get("access_token") or data.get("id_token")
    except Exception:
        logger.exception("Custom OIDC: failed to fetch token from %s", url)
        return None
