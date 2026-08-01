"""Regression contracts for non-temporary Bandit MEDIUM remediations.

These tests stay offline: outbound HTTP, cloud SDKs, and live sockets are all
replaced with local fakes.  The matching ``make sast`` run remains the final
scanner-level proof that no B104/B310/B323/B608 finding is suppressed.
"""

from __future__ import annotations

import json
import shlex
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.config.user_config import NetworkConfig
from general_ludd.history import git_indexer
from general_ludd.infra import azure_retail_pricing, discovery, terraform
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.networking import scapy_adapter
from general_ludd.onboard import azure as azure_onboard
from general_ludd.onboard import gcp as gcp_onboard

_SCOPED_SOURCES = (
    "src/general_ludd/config/user_config.py",
    "src/general_ludd/daemon.py",
    "src/general_ludd/history/git_indexer.py",
    "src/general_ludd/infra/azure_retail_pricing.py",
    "src/general_ludd/infra/discovery.py",
    "src/general_ludd/infra/terraform.py",
    "src/general_ludd/networking/scapy_adapter.py",
    "src/general_ludd/onboard/azure.py",
    "src/general_ludd/onboard/gcp.py",
)


def test_scoped_sources_do_not_suppress_medium_bandit_findings() -> None:
    repo = Path(__file__).resolve().parents[2]
    for relative_path in _SCOPED_SOURCES:
        source = (repo / relative_path).read_text(encoding="utf-8")
        for rule in ("B104", "B310", "B323", "B608"):
            assert f"nosec {rule}" not in source, f"{relative_path} suppresses {rule}"


@pytest.mark.parametrize(
    ("host", "allowed_cidr", "expected"),
    [
        ("127.0.0.1", [], False),
        ("::1", [], False),
        ("localhost", [], False),
        ("0.0.0.0", ["10.0.0.0/8"], True),
        ("::", ["2001:db8::/32"], True),
        ("192.168.1.10", ["192.168.1.0/24"], True),
    ],
)
def test_network_config_classifies_external_binds(
    host: str,
    allowed_cidr: list[str],
    expected: bool,
) -> None:
    network = NetworkConfig(host=host, allowed_cidr=allowed_cidr)
    assert network.is_external_bind is expected


def test_daemon_external_bind_requires_configured_authentication() -> None:
    from general_ludd import daemon

    configure = daemon._configure_network_state
    network = NetworkConfig(host="0.0.0.0", allowed_cidr=["10.0.0.0/8"])
    unauthenticated = SimpleNamespace(
        state=SimpleNamespace(_allowed_cidr=[], _no_auth=True),
    )
    with pytest.raises(RuntimeError, match="authenticated"):
        configure(unauthenticated, network)

    authenticated = SimpleNamespace(
        state=SimpleNamespace(_allowed_cidr=[], _no_auth=False),
    )
    configure(authenticated, network)
    assert authenticated.state._allowed_cidr == ["10.0.0.0/8"]
    assert authenticated.state._network_host == network.host


def test_terraform_bind_is_loopback_until_ingress_is_explicitly_external() -> None:
    loopback_config = ComputeConfig(
        provider=ComputeProvider.AWS,
        gpu_type=GPUType.T4,
        model_name="org/model",
    )
    loopback_tokens = shlex.split(terraform._engine_serve_cmd(loopback_config))
    host_index = loopback_tokens.index("--host")
    assert loopback_tokens[host_index + 1] == terraform._LOOPBACK_IPV4

    external_config = loopback_config.model_copy(
        update={"allowed_cidr": "10.0.0.0/8"},
    )
    external_tokens = shlex.split(terraform._engine_serve_cmd(external_config))
    host_index = external_tokens.index("--host")
    assert external_tokens[host_index + 1] == terraform._UNSPECIFIED_IPV4


def test_pcap_missing_addresses_use_protocol_unspecified_value() -> None:
    assert scapy_adapter._UNSPECIFIED_IPV4 == terraform._UNSPECIFIED_IPV4


def test_git_history_search_uses_one_static_parameterized_statement(tmp_path: Path) -> None:
    sql = git_indexer._SEARCH_SQL
    assert "WHERE (? = '' OR" in sql
    assert "LIMIT ? OFFSET ?" in sql

    indexer = git_indexer.GitHistoryIndexer(tmp_path, tmp_path / "history.db")
    conn = indexer._get_conn()
    try:
        conn.executemany(
            "INSERT INTO commits(hash, author, date, message, insertions, deletions) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            [
                ("one", "Ada", "2026-01-01T00:00:00+00:00", "secure", 1, 0),
                ("two", "Lin", "2026-01-02T00:00:00+00:00", "feature", 1, 0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    assert indexer.search(query="' OR 1=1 --") == []


def test_azure_default_fetcher_delegates_to_central_secure_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload: dict[str, object] = {"Items": [], "NextPageLink": None, "Count": 0}
    seen: dict[str, Any] = {}

    def fake_secure_fetch(url: str, *, policy: Any, **_kwargs: Any) -> Any:
        seen["url"] = url
        seen["policy"] = policy
        return SimpleNamespace(
            url=url,
            status_code=200,
            headers={"content-type": "application/json"},
            content=json.dumps(payload).encode(),
        )

    monkeypatch.setattr(azure_retail_pricing, "secure_fetch", fake_secure_fetch, raising=False)
    assert azure_retail_pricing.urlopen is azure_retail_pricing._secure_urlopen

    result = azure_retail_pricing.AzureContainerAppsRetailPricing._default_fetch_json(
        "https://prices.azure.com/api/retail/prices",
        2.5,
    )
    assert result == payload
    assert seen["url"] == "https://prices.azure.com/api/retail/prices"
    policy = seen["policy"]
    assert policy.allowed_hosts == frozenset({"prices.azure.com"})
    assert policy.allowed_schemes == frozenset({"https"})
    assert policy.timeout_seconds == 2.5


def test_vsphere_opt_out_uses_public_ssl_context_api(
    caplog: pytest.LogCaptureFixture,
) -> None:
    verified = discovery._build_vsphere_ssl_context(verify_ssl=True)
    assert verified is None

    unverified = discovery._build_vsphere_ssl_context(verify_ssl=False)
    assert isinstance(unverified, ssl.SSLContext)
    assert unverified.check_hostname is False
    assert unverified.verify_mode == ssl.CERT_NONE
    assert "explicitly disabled" in caplog.text


@pytest.mark.parametrize(
    ("builder", "kwargs"),
    [
        (
            azure_onboard.create_role_instructions,
            {"subscription_id": "safe-id\naz account clear"},
        ),
        (
            gcp_onboard.create_role_instructions,
            {"project_id": "safe-project\ngcloud projects delete victim"},
        ),
    ],
)
def test_onboarding_instructions_reject_command_injection(
    builder: Any,
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        builder(**kwargs)
