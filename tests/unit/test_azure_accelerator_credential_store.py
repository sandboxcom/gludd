"""Durability and non-deletion contracts for Azure controller credentials."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import general_ludd.azure.accelerator_credential_store as store_module
from general_ludd.azure.accelerator_credential_store import (
    AzureAcceleratorCredentialStore,
    AzureCredentialArtifactError,
    AzureCredentialArtifactState,
    default_azure_accelerator_credential_home,
    load_preserved_azure_accelerator_credentials,
)

SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
TENANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLIENT_ID = "01234567-89ab-cdef-0123-456789abcdef"
OTHER_CLIENT_ID = "11234567-89ab-cdef-0123-456789abcdef"
SECRET = "private-first-generation"
OTHER_SECRET = "private-second-generation"
ROOT = Path(__file__).resolve().parents[2]


def _payload(*, client_id: str = CLIENT_ID, secret: str = SECRET) -> bytes:
    return json.dumps(
        {
            "clientId": client_id,
            "clientSecret": secret,
            "subscriptionId": SUBSCRIPTION_ID,
            "tenantId": TENANT_ID,
        }
    ).encode()


def _test_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **kwargs: object,
) -> AzureAcceleratorCredentialStore:
    monkeypatch.setattr(store_module, "_reject_unsafe_root", lambda _root: None)
    return AzureAcceleratorCredentialStore(root=tmp_path / "credentials", **kwargs)


def test_default_home_is_persistent_xdg_data_not_runtime_or_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "durable-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "runtime-temp"))
    monkeypatch.delenv("GLUDD_CREDENTIAL_HOME", raising=False)

    assert default_azure_accelerator_credential_home() == (
        data_home / "general-ludd" / "credentials"
    )


@pytest.mark.parametrize("unsafe_name", ["tmp", "var-tmp", "run", "worktree"])
def test_store_refuses_ephemeral_or_worktree_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_name: str,
) -> None:
    root = tmp_path / unsafe_name
    if unsafe_name == "worktree":
        root.mkdir()
        (root / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    monkeypatch.setattr(
        store_module,
        "_ephemeral_roots",
        lambda: (tmp_path / "tmp", tmp_path / "var-tmp", tmp_path / "run"),
    )

    with pytest.raises(AzureCredentialArtifactError, match="durable credential root"):
        AzureAcceleratorCredentialStore(root=root)


def test_install_keeps_immutable_generation_and_owner_private_current_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    store = _test_store(tmp_path, monkeypatch, trace_sink=events.append)

    receipt = store.install(_payload(), expected_subscription_id=SUBSCRIPTION_ID)

    assert receipt.current_path == store.current_path
    assert receipt.generation_path.exists()
    assert receipt.current_path.exists()
    assert receipt.generation_path.stat().st_ino == receipt.current_path.stat().st_ino
    assert receipt.generation_path.stat().st_mode & 0o777 == 0o600
    assert receipt.current_path.read_bytes() == _payload()
    assert [event.state for event in events] == [
        AzureCredentialArtifactState.STAGED,
        AzureCredentialArtifactState.ACTIVATED,
    ]
    assert SECRET not in "".join(map(repr, events))


def test_rotation_preserves_every_generation_across_fresh_store_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    first = store.install(_payload(), expected_subscription_id=SUBSCRIPTION_ID)
    second = store.install(
        _payload(client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET),
        expected_subscription_id=SUBSCRIPTION_ID,
    )

    restarted = _test_store(tmp_path, monkeypatch)
    loaded = restarted.load_current(expected_subscription_id=SUBSCRIPTION_ID)

    assert loaded.client_id == OTHER_CLIENT_ID
    assert first.generation_path.read_bytes() == _payload()
    assert second.generation_path.read_bytes() == _payload(
        client_id=OTHER_CLIENT_ID,
        secret=OTHER_SECRET,
    )
    assert len(restarted.generation_paths()) == 2


def test_missing_current_is_restored_from_active_manifest_without_generation_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    receipt = store.install(_payload(), expected_subscription_id=SUBSCRIPTION_ID)
    store.current_path.unlink()

    restarted = _test_store(tmp_path, monkeypatch)
    loaded = restarted.load_current(expected_subscription_id=SUBSCRIPTION_ID)

    assert loaded.client_id == CLIENT_ID
    assert restarted.current_path.exists()
    assert restarted.current_path.stat().st_ino == receipt.generation_path.stat().st_ino
    assert receipt.generation_path.read_bytes() == _payload()


def test_invalid_replacement_cannot_modify_current_or_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    receipt = store.install(_payload(), expected_subscription_id=SUBSCRIPTION_ID)
    before_inode = store.current_path.stat().st_ino
    before_generations = store.generation_paths()

    with pytest.raises(AzureCredentialArtifactError, match="valid JSON"):
        store.install(b"{", expected_subscription_id=SUBSCRIPTION_ID)

    assert store.current_path.stat().st_ino == before_inode
    assert store.current_path.read_bytes() == _payload()
    assert store.generation_paths() == before_generations
    assert receipt.generation_path.exists()


def test_interrupted_activation_preserves_old_current_and_new_staged_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    first = store.install(_payload(), expected_subscription_id=SUBSCRIPTION_ID)
    real_replace = store_module.os.replace

    def fail_current_activation(source: str | Path, target: str | Path) -> None:
        if Path(target) == store.current_path:
            raise OSError("simulated activation crash")
        real_replace(source, target)

    monkeypatch.setattr(store_module.os, "replace", fail_current_activation)
    with pytest.raises(AzureCredentialArtifactError, match="activation failed"):
        store.install(
            _payload(client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET),
            expected_subscription_id=SUBSCRIPTION_ID,
        )

    assert store.current_path.read_bytes() == _payload()
    assert first.generation_path.exists()
    assert len(store.generation_paths()) == 2
    assert any(
        path.read_bytes() == _payload(client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET)
        for path in store.generation_paths()
    )


def test_existing_legacy_current_is_archived_before_first_managed_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    store.current_path.write_bytes(_payload())
    store.current_path.chmod(0o600)

    store.install(
        _payload(client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET),
        expected_subscription_id=SUBSCRIPTION_ID,
    )

    generation_payloads = {path.read_bytes() for path in store.generation_paths()}
    assert generation_payloads == {
        _payload(),
        _payload(client_id=OTHER_CLIENT_ID, secret=OTHER_SECRET),
    }


def test_audit_log_is_content_free_and_persists_recovery_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    store.install(_payload(), expected_subscription_id=SUBSCRIPTION_ID)
    store.current_path.unlink()

    _test_store(tmp_path, monkeypatch).load_current(
        expected_subscription_id=SUBSCRIPTION_ID
    )

    audit = store.audit_path.read_text(encoding="utf-8")
    assert SECRET not in audit
    assert CLIENT_ID not in audit
    assert '"state":"restored"' in audit
    assert audit.count("\n") == 3
    assert os.stat(store.audit_path).st_mode & 0o777 == 0o600


def test_manifest_symlink_cannot_redirect_active_generation_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    receipt = store.install(_payload(), expected_subscription_id=SUBSCRIPTION_ID)
    manifest = store.manifest_path.read_bytes()
    store.current_path.unlink()
    store.manifest_path.unlink()
    attacker_manifest = tmp_path / "attacker-manifest.json"
    attacker_manifest.write_bytes(manifest)
    store.manifest_path.symlink_to(attacker_manifest)

    with pytest.raises(
        AzureCredentialArtifactError,
        match="active credential manifest is unavailable",
    ):
        store.load_current(expected_subscription_id=SUBSCRIPTION_ID)

    assert not store.current_path.exists()
    assert receipt.generation_path.read_bytes() == _payload()


def test_generation_inventory_ignores_symlink_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    receipt = store.install(_payload(), expected_subscription_id=SUBSCRIPTION_ID)
    alias = receipt.generation_path.parent / ("f" * 32 + ".json")
    alias.symlink_to(receipt.generation_path)

    assert store.generation_paths() == (receipt.generation_path,)


def test_audit_descriptor_is_closed_when_target_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    closed: list[int] = []
    real_close = store_module.os.close
    real_fstat = store_module.os.fstat

    def nonregular(descriptor: int) -> os.stat_result:
        opened = real_fstat(descriptor)
        values = list(opened)
        values[0] = stat.S_IFDIR | 0o700
        return os.stat_result(values)

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(store_module.os, "fstat", nonregular)
    monkeypatch.setattr(store_module.os, "close", record_close)

    with pytest.raises(AzureCredentialArtifactError, match="audit failed"):
        store._append_audit(
            store_module.AzureCredentialArtifactEvent(
                state=AzureCredentialArtifactState.STAGED,
                generation_id="a" * 32,
            )
        )

    assert len(closed) == 1


def test_preserved_loader_restores_managed_current_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _test_store(tmp_path, monkeypatch)
    receipt = store.install(_payload(), expected_subscription_id=SUBSCRIPTION_ID)
    store.current_path.unlink()

    loaded = load_preserved_azure_accelerator_credentials(
        store.current_path,
        expected_subscription_id=SUBSCRIPTION_ID,
    )

    assert loaded.client_id == CLIENT_ID
    assert store.current_path.stat().st_ino == receipt.generation_path.stat().st_ino


def test_preserved_loader_retains_legacy_private_file_compatibility(
    tmp_path: Path,
) -> None:
    credential = tmp_path / "legacy.json"
    credential.write_bytes(_payload())
    credential.chmod(0o600)

    loaded = load_preserved_azure_accelerator_credentials(
        credential,
        expected_subscription_id=SUBSCRIPTION_ID,
    )

    assert loaded.client_id == CLIENT_ID


@pytest.mark.parametrize(
    "consumer",
    (
        "scripts/validate_azure_accelerator_credentials.py",
        "scripts/azure_containerapp_preflight.py",
        "scripts/azure_containerapp_live_proof.py",
        "src/general_ludd/self_improve/azure_containerapp_bootstrap_credentials.py",
        "src/general_ludd/self_improve/azure_containerapp_bootstrap.py",
    ),
)
def test_every_file_credential_consumer_uses_recoverable_loader(consumer: str) -> None:
    source = (ROOT / consumer).read_text(encoding="utf-8")

    assert "load_preserved_azure_accelerator_credentials" in source
