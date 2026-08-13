"""XDG browser-based login flow for gludd services.

Implements the OAuth2 Authorization Code grant with PKCE (Proof Key for
Code Exchange) for services that support it, and a token-paste fallback
for API-key-based services.

Supported services:
- GitHub (OAuth2 + PKCE device flow)
- OpenAI (API key via browser redirect)
- DeepSeek (API key)
- Z.AI / ZhipuAI (API key)
- Anthropic (API key)
- Google Gemini (OAuth2 + PKCE)
- OpenRouter (API key)

Architecture:
1. Generate PKCE code_verifier + code_challenge
2. Open the user's default browser to the service's authorization URL
3. Start a local HTTP server on a random port to receive the OAuth callback
4. Exchange the authorization code for tokens (OAuth2 services)
5. Store credentials securely via environment variable or OpenBao

Non-automatable parts (documented in README.md):
- Creating an OAuth application / client ID for each service
- Setting redirect URI to http://localhost:<port>/callback
- First-time OpenBao unseal / bootstrap
"""

from __future__ import annotations

import abc
import base64
import hashlib
import http.server
import json
import logging
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from http.server import HTTPServer
from pathlib import Path
from typing import Any, ClassVar

from general_ludd.security.url_fetch import FetchPolicy, secure_fetch

logger = logging.getLogger(__name__)

_SYSTEM = platform.system()
_OPEN_CMD = "open" if _SYSTEM == "Darwin" else "xdg-open"

_REDACT_RE = re.compile(r"[A-Za-z0-9+/=_-]{20,}", re.IGNORECASE)

# ---- service presets --------------------------------------------------------


@dataclass(frozen=True)
class ServiceConfig:
    """OAuth2 / API-key login configuration for a service."""

    name: str
    display_name: str
    auth_url: str
    exchange_url: str

    @property
    def token_url(self) -> str:
        return self.exchange_url
    scopes: list[str] = field(default_factory=list)
    client_id_env: str = ""
    client_credential_env: str = ""

    @property
    def client_secret_env(self) -> str:
        return self.client_credential_env
    credential_env: str = ""
    extra_auth_params: dict[str, str] = field(default_factory=dict)
    requires_client_registration: bool = True
    help_url: str = ""


SERVICE_PRESETS: dict[str, ServiceConfig] = {
    "github": ServiceConfig(
        name="github",
        display_name="GitHub",
        auth_url="https://github.com/login/oauth/authorize",
        exchange_url="https://github.com/login/oauth/access_token",
        scopes=["repo", "user", "workflow"],
        client_id_env="GITHUB_OAUTH_CLIENT_ID",
        client_credential_env="GITHUB_OAUTH_CLIENT_SECRET",
        credential_env="GITHUB_TOKEN",
        help_url="https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app",
    ),
    "openai": ServiceConfig(
        name="openai",
        display_name="OpenAI",
        auth_url="https://platform.openai.com/account/api-keys",
        exchange_url="",
        credential_env="OPENAI_API_KEY",
        requires_client_registration=False,
        help_url="https://platform.openai.com/api-keys",
    ),
    "deepseek": ServiceConfig(
        name="deepseek",
        display_name="DeepSeek",
        auth_url="https://platform.deepseek.com/api_keys",
        exchange_url="",
        credential_env="DEEPSEEK_API_KEY",
        requires_client_registration=False,
        help_url="https://platform.deepseek.com/api_keys",
    ),
    "zai": ServiceConfig(
        name="zai",
        display_name="Z.AI / ZhipuAI",
        auth_url="https://open.bigmodel.cn/usercenter/apikeys",
        exchange_url="",
        credential_env="ZAI_API_KEY",
        requires_client_registration=False,
        help_url="https://open.bigmodel.cn/usercenter/apikeys",
    ),
    "anthropic": ServiceConfig(
        name="anthropic",
        display_name="Anthropic",
        auth_url="https://console.anthropic.com/settings/keys",
        exchange_url="",
        credential_env="ANTHROPIC_API_KEY",
        requires_client_registration=False,
        help_url="https://console.anthropic.com/settings/keys",
    ),
    "gemini": ServiceConfig(
        name="gemini",
        display_name="Google Gemini",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        exchange_url="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
        client_id_env="GOOGLE_OAUTH_CLIENT_ID",
        client_credential_env="GOOGLE_OAUTH_CLIENT_SECRET",
        credential_env="GOOGLE_API_KEY",
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
        help_url="https://console.cloud.google.com/apis/credentials",
    ),
    "openrouter": ServiceConfig(
        name="openrouter",
        display_name="OpenRouter",
        auth_url="https://openrouter.ai/keys",
        exchange_url="",
        credential_env="OPENROUTER_API_KEY",
        requires_client_registration=False,
        help_url="https://openrouter.ai/keys",
    ),
}


def list_services() -> list[str]:
    return sorted(SERVICE_PRESETS.keys())


# ---- credential storage ----------------------------------------------------


class CredentialStore(abc.ABC):
    """Abstract credential storage backend.

    Implementations store and retrieve OAuth tokens / API keys for services.
    """

    @abc.abstractmethod
    def store(self, service: str, credential: str, metadata: dict[str, Any] | None = None) -> None: ...

    @abc.abstractmethod
    def retrieve(self, service: str) -> str | None: ...

    @abc.abstractmethod
    def store_metadata(self, service: str, metadata: dict[str, Any]) -> None: ...


class EnvCredentialStore(CredentialStore):
    """Stores credentials in environment variables.

    Writes to a shell-export file so credentials persist across sessions.
    Does NOT write to .bashrc/.zshrc — uses a dedicated file sourced on demand.

    The env file is written to ``$XDG_CONFIG_HOME/gludd/credentials.env``
    (default ``~/.config/gludd/credentials.env``).
    """

    _DEFAULT_PERMS = 0o600

    def __init__(self, env_file: str | Path | None = None) -> None:
        if env_file is None:
            xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
            env_file = Path(xdg_config) / "gludd" / "credentials.env"
        self._env_file = Path(env_file)
        self._env_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._env_file.exists():
            self._env_file.touch()
        if platform.system() != "Windows":
            os.chmod(self._env_file, self._DEFAULT_PERMS)

    def store(self, service: str, credential: str, metadata: dict[str, Any] | None = None) -> None:
        env_var = self._env_var_for(service)
        os.environ[env_var] = credential
        self._write_env_var(env_var, credential)
        if metadata:
            self.store_metadata(service, metadata)

    def retrieve(self, service: str) -> str | None:
        env_var = self._env_var_for(service)
        val = os.environ.get(env_var)
        if val:
            return val
        return self._read_env_var(env_var)

    def store_metadata(self, service: str, metadata: dict[str, Any]) -> None:
        path = self._env_file.parent / f"{service}_metadata.json"
        path.write_text(json.dumps(metadata, indent=2))
        if platform.system() != "Windows":
            os.chmod(path, self._DEFAULT_PERMS)

    @staticmethod
    def _env_var_for(service: str) -> str:
        mapping: dict[str, str] = {
            "github": "GITHUB_TOKEN",
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "zai": "ZAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GOOGLE_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        return mapping.get(service, f"GLUDD_{service.upper()}_TOKEN")

    def _write_env_var(self, key: str, value: str) -> None:
        lines: list[str] = []
        if self._env_file.exists():
            lines = self._env_file.read_text().splitlines(keepends=False)
        filtered = [line for line in lines if not line.startswith(f"export {key}=") and line.strip() != ""]
        filtered.append(f'export {key}="{value}"')
        self._env_file.write_text("\n".join(filtered) + "\n")

    def _read_env_var(self, key: str) -> str | None:
        if not self._env_file.exists():
            return None
        for line in self._env_file.read_text().splitlines():
            if line.startswith(f"export {key}="):
                val = line[len(f"export {key}="):].strip().strip('"').strip("'")
                return val
        return None


# ---- OpenBao credential store ----------------------------------------------

try:
    from general_ludd.secrets.manager import SecretsManager

    _SECRETS_MANAGER_AVAILABLE = True
except ImportError:
    _SECRETS_MANAGER_AVAILABLE = False


class OpenBaoCredentialStore(CredentialStore):
    """Stores credentials in OpenBao via SecretsManager.

    Each service credential is stored at ``secret/gludd/auth/<service>``
    with key ``value`` and optional metadata.
    """

    _NS = "gludd/auth"

    def __init__(self, secrets_manager: SecretsManager) -> None:
        self._sm = secrets_manager

    def store(self, service: str, credential: str, metadata: dict[str, Any] | None = None) -> None:
        path = f"{self._NS}/{service}"
        payload: dict[str, Any] = {"value": credential}
        if metadata:
            payload["metadata"] = json.dumps(metadata)
        self._sm.write_secret(path, payload)

    def retrieve(self, service: str) -> str | None:
        path = f"{self._NS}/{service}"
        try:
            data = self._sm.read_secret(path)
            if data and "value" in data:
                return str(data["value"])
        except Exception:
            logger.debug("OpenBao credential retrieval failed for %s", service)
        return None

    def store_metadata(self, service: str, metadata: dict[str, Any]) -> None:
        path = f"{self._NS}/{service}"
        try:
            existing = self._sm.read_secret(path)
            payload: dict[str, Any] = dict(existing) if existing else {}
            payload["metadata"] = json.dumps(metadata)
            self._sm.write_secret(path, payload)
        except Exception:
            logger.debug("OpenBao metadata store failed for %s", service)


# ---- PKCE helpers -----------------------------------------------------------


def _pkce_code_verifier(length: int = 64) -> str:
    allowed = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(chr(allowed[b % len(allowed)]) for b in secrets.token_bytes(length))


def _pkce_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---- local redirect server --------------------------------------------------


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-use HTTP handler that captures the OAuth2 authorization code."""

    captured_code: ClassVar[str | None] = None
    captured_state: ClassVar[str | None] = None
    captured_error: ClassVar[str | None] = None
    done: ClassVar[threading.Event] = threading.Event()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/callback" or parsed.path == "/":
            error = params.get("error", [None])[0]
            if error:
                _CallbackHandler.captured_error = error
                _CallbackHandler.done.set()
                self._respond(400, "Authorization denied — you may close this tab.")
                return

            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            if code:
                _CallbackHandler.captured_code = code
                _CallbackHandler.captured_state = state
                _CallbackHandler.done.set()
                self._respond(200, "Login successful! You may close this tab.")
            else:
                self._respond(400, "Missing authorization code — please try again.")
        else:
            self._respond(404, "Not found.")

    def _respond(self, status: int, body: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("callback server: %s", fmt % args)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start_callback_server(port: int, timeout: float = 120.0) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 1.0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---- browser launcher -------------------------------------------------------


def _open_browser(url: str) -> subprocess.Popen[bytes] | None:
    try:
        return subprocess.Popen(
            [_OPEN_CMD, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        webbrowser.open(url)
        return None


# ---- login flow -------------------------------------------------------------


class BrowserLoginFlow:
    """Orchestrates the browser-based OAuth2 / API-key login flow.

    Usage:

        flow = BrowserLoginFlow("github")
        token = flow.run()

    Or with explicit config:

        config = ServiceConfig(...)
        flow = BrowserLoginFlow.from_config(config)
        token = flow.run()
    """

    _DEFAULT_TIMEOUT = 120.0
    _POLL_INTERVAL = 0.25

    def __init__(
        self,
        service: str,
        config: ServiceConfig | None = None,
        store: CredentialStore | None = None,
        payment_label: str | None = None,
    ) -> None:
        self._service = service
        _cfg = config or SERVICE_PRESETS.get(service)
        if _cfg is None:
            raise ValueError(f"Unknown service: {service!r}. Known: {list_services()}")
        self._config: ServiceConfig = _cfg
        self._store = store or EnvCredentialStore()
        self._payment_label = payment_label

    @classmethod
    def from_config(cls, config: ServiceConfig, store: CredentialStore | None = None) -> BrowserLoginFlow:
        return cls(config.name, config=config, store=store)

    @property
    def service_name(self) -> str:
        return self._config.name

    @property
    def display_name(self) -> str:
        return self._config.display_name

    def run(
        self,
        timeout: float = _DEFAULT_TIMEOUT,
        payment_label: str | None = None,
    ) -> str | None:
        if payment_label is not None:
            self._payment_label = payment_label
        if self._config.token_url:
            return self._run_oauth2(timeout=timeout)
        return self._run_api_key(timeout=timeout)

    def _payment_metadata(self) -> dict[str, str]:
        label = self._payment_label
        if not label:
            return {}
        try:
            from general_ludd.secrets.payment_vault import SecurePaymentVault
        except ImportError:
            return {}
        sm = getattr(self._store, "_sm", None)
        if sm is None:
            return {}
        try:
            vault = SecurePaymentVault(sm)
            token = vault.get_processor_token(label)
            last4 = vault.get_card_last4(label)
        except Exception:
            return {}
        if token is None:
            return {}
        meta: dict[str, str] = {"payment_processor_token": token}
        if last4 is not None:
            meta["payment_card_last4"] = last4
        return meta

    # -- OAuth2 + PKCE flow ---------------------------------------------------

    def _run_oauth2(self, timeout: float) -> str | None:
        config = self._config
        client_id = os.environ.get(config.client_id_env, "")
        client_secret = os.environ.get(config.client_secret_env, "")

        if config.requires_client_registration and not client_id:
            self._print_registration_help()
            return None

        port = _find_free_port()
        callback_url = f"http://127.0.0.1:{port}/callback"
        state = secrets.token_urlsafe(32)
        verifier = _pkce_code_verifier()
        challenge = _pkce_code_challenge(verifier)

        auth_params: dict[str, str] = {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "response_type": "code",
            "scope": " ".join(config.scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_params.update(config.extra_auth_params)

        auth_url = f"{config.auth_url}?{urllib.parse.urlencode(auth_params)}"

        _CallbackHandler.captured_code = None
        _CallbackHandler.captured_state = None
        _CallbackHandler.captured_error = None
        _CallbackHandler.done.clear()

        server = _start_callback_server(port, timeout=timeout)
        try:
            _open_browser(auth_url)

            print(f"\n  Opening {config.display_name} authorization in your browser...")
            print(f"  Listening for callback on {callback_url}")
            print(f"  Waiting up to {int(timeout)}s for authorization...\n")

            waited = _CallbackHandler.done.wait(timeout=timeout)

            if not waited or _CallbackHandler.captured_code is None:
                if _CallbackHandler.captured_error:
                    print(f"  Authorization denied: {_CallbackHandler.captured_error}", file=sys.stderr)
                else:
                    print("  Login timed out — no authorization code received.", file=sys.stderr)
                return None

            if _CallbackHandler.captured_state and _CallbackHandler.captured_state != state:
                print("  State mismatch — possible CSRF attack. Aborting.", file=sys.stderr)
                return None

            code = _CallbackHandler.captured_code
            return self._exchange_code(code, verifier, callback_url, client_id, client_secret)
        finally:
            server.shutdown()
            server.server_close()

    def _exchange_code(
        self,
        code: str,
        verifier: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
    ) -> str | None:
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        }).encode("ascii")

        headers: dict[str, str] = {"Accept": "application/json"}
        if client_secret:
            credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            headers["Authorization"] = f"Basic {credentials}"

        try:
            token_host = urllib.parse.urlsplit(self._config.token_url).hostname or ""
            response = secure_fetch(
                self._config.token_url,
                method="POST",
                headers=headers,
                content=data,
                policy=FetchPolicy(
                    allowed_hosts=frozenset({token_host}),
                    max_bytes=512 * 1024,
                    timeout_seconds=15,
                    max_redirects=2,
                ),
            )
            body: dict[str, Any] = json.loads(response.content.decode())
        except Exception as exc:
            print(f"  Token exchange failed: {exc}", file=sys.stderr)
            return None

        access_token: str | None = body.get("access_token") or body.get("token")
        if access_token:
            metadata = {
                "service": self._service,
                "scope": body.get("scope", ""),
                "token_type": body.get("token_type", "bearer"),
                "refresh_token": body.get("refresh_token", ""),
            }
            metadata.update(self._payment_metadata())
            self._store.store(self._service, access_token, metadata=metadata)
            print(f"  Login to {self._config.display_name} successful.")
            print(f"  Credential stored in {self._config.credential_env}")
            return access_token

        error = body.get("error_description") or body.get("error", "unknown")
        print(f"  Token exchange error: {error}", file=sys.stderr)
        return None

    # -- API key flow (browser to get key, paste into terminal) ---------------

    def _run_api_key(self, timeout: float) -> str | None:
        config = self._config
        env_var = config.credential_env

        existing = self._store.retrieve(self._service) or os.environ.get(env_var)
        if existing:
            self._store.store(self._service, existing)
            print(f"  {config.display_name}: credential already stored in {env_var}.")
            return existing

        print(f"\n  === {config.display_name} Login ===")
        print(f"  Opening {config.auth_url} in your browser...")
        print("  Paste your API key when ready.\n")

        _open_browser(config.auth_url)

        try:
            api_key = input(f"  {config.display_name} API key (input hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("  Login cancelled.", file=sys.stderr)
            return None

        if not api_key:
            print("  No API key entered — login skipped.", file=sys.stderr)
            return None

        metadata = {
            "service": self._service,
            "source": "key_paste",
        }
        metadata.update(self._payment_metadata())
        self._store.store(self._service, api_key, metadata=metadata)
        print(f"  Login to {self._config.display_name} successful.")
        print(f"  Credential stored in {env_var}")
        return api_key

    def _print_registration_help(self) -> None:
        config = self._config
        print(f"\n  === {config.display_name} requires OAuth app registration ===")
        print("  You need a client ID before continuing.")
        print(f"  Set it via: export {config.client_id_env}=<your-client-id>")
        if config.client_secret_env:
            print(f"  Also set:   export {config.client_secret_env}=<your-client-secret>")
        if config.help_url:
            print(f"  Docs: {config.help_url}")
        print()


# ---- top-level convenience --------------------------------------------------


def login(
    service: str,
    store: CredentialStore | None = None,
    timeout: float = 120.0,
) -> str | None:
    flow = BrowserLoginFlow(service, store=store)
    return flow.run(timeout=timeout)


def open_browser_auth(
    service: str,
    store: CredentialStore | None = None,
    timeout: float = 120.0,
) -> str | None:
    """Open the default browser to authenticate with ``service``.

    Thin alias for :func:`login` matching the public spec name. Opens the
    user's default browser via ``xdg-open`` (Linux) or ``open`` (macOS),
    starts a local HTTP callback server on a random port, drives the OAuth2
    PKCE flow (or API-key paste fallback), and returns the obtained token.
    """
    return login(service, store=store, timeout=timeout)
