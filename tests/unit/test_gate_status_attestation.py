"""TDD contract for gate attestations bound to the tested repository state."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import gate_status_attestation as attestation
from scripts.gate_status_attestation import (
    repository_state_id,
    sign_status,
    sign_terminal_status,
    verify_status,
    verify_terminal_status,
)
from scripts.makefile_layout import compose_makefile

_KEY = b"k" * 32
_STATE = "state-" + "a" * 64


def _passed_status(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                "=== GATE 2026-08-12T00:00:00Z ===",
                "lint PASS 0",
                "typecheck PASS 0",
                "collect PASS 0",
                "test PASS 0",
                "smoke PASS",
                "---",
                "epoch 1786492800",
                "=== GATE: PASSED ===",
                "",
            )
        ),
        encoding="utf-8",
    )



def _resume_status(path: Path, *, now: int = 1000, policy: str | None = None) -> None:
    path.write_text(
        "=== GATE fixture ===\n"
        + (policy if policy is not None else
           "gate-policy profile=release max_files_per_batch=16 workers=1\n")
        + "preflights PASS 35\nlint FAIL 1\n=== GATE: FAILED ===\n",
        encoding="utf-8",
    )
    sign_terminal_status(path, state_id=_STATE, key=_KEY, now=now)


def test_resume_requires_current_signed_policy_without_admitting_failed_gate(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _resume_status(status)
    result = attestation.verify_resume_status(
        status, state_id=_STATE, key=_KEY, profile="release",
        max_files_per_batch=16, workers=1, now=1001, freshness_seconds=60,
    )
    assert result.ok, result.reason
    assert verify_terminal_status(status, state_id=_STATE, key=_KEY, now=1001).ok
    assert not verify_status(status, state_id=_STATE, key=_KEY, now=1001).ok


@pytest.mark.parametrize(
    ("mutation", "state", "key", "now"),
    [
        ("unsigned", _STATE, _KEY, 1001),
        ("tamper", _STATE, _KEY, 1001),
        ("policy", _STATE, _KEY, 1001),
        ("duplicate-policy", _STATE, _KEY, 1001),
        ("missing-policy", _STATE, _KEY, 1001),
        ("failed-preflight", _STATE, _KEY, 1001),
        ("duplicate-preflight", _STATE, _KEY, 1001),
        ("unchanged", "different-state", _KEY, 1001),
        ("unchanged", _STATE, b"x" * 32, 1001),
        ("unchanged", _STATE, _KEY, 1061),
    ],
)
def test_resume_refuses_unsigned_stale_changed_or_incomplete_evidence(
    tmp_path: Path, mutation: str, state: str, key: bytes, now: int,
) -> None:
    status = tmp_path / ".gate-status"
    _resume_status(status)
    original = status.read_text(encoding="utf-8")
    changed = original
    if mutation == "unsigned":
        changed = original.split("attestation-version", 1)[0]
    elif mutation == "tamper":
        changed = original.replace("preflights PASS 35", "preflights PASS 36")
    elif mutation == "policy":
        _resume_status(status, policy="gate-policy profile=native max_files_per_batch=16 workers=1\n")
    elif mutation == "duplicate-policy":
        _resume_status(status, policy=(
            "gate-policy profile=release max_files_per_batch=16 workers=1\n" * 2
        ))
    elif mutation == "missing-policy":
        _resume_status(status, policy="")
    elif mutation == "failed-preflight":
        changed = original.split("attestation-version", 1)[0].replace("preflights PASS 35", "preflights FAIL 1")
        status.write_text(changed, encoding="utf-8")
        sign_terminal_status(status, state_id=_STATE, key=_KEY, now=1000)
    elif mutation == "duplicate-preflight":
        changed = original.split("attestation-version", 1)[0].replace(
            "preflights PASS 35", "preflights PASS 35\npreflights FAIL 1",
        )
        status.write_text(changed, encoding="utf-8")
        sign_terminal_status(status, state_id=_STATE, key=_KEY, now=1000)
    if mutation in {"unsigned", "tamper"}:
        status.write_text(changed, encoding="utf-8")
    result = attestation.verify_resume_status(
        status, state_id=state, key=key, profile="release",
        max_files_per_batch=16, workers=1, now=now, freshness_seconds=60,
    )
    assert not result.ok
    assert result.reason


def test_resume_policy_uses_the_authenticated_captured_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = tmp_path / ".gate-status"
    _resume_status(status)
    captured = status.read_text(encoding="utf-8")
    split = attestation._split_status
    observations: list[str] = []

    def replace_after_capture(content: str) -> tuple[str, dict[str, str], str | None]:
        observations.append(content)
        status.write_text("preflights PASS 999\n", encoding="utf-8")
        return split(content)

    monkeypatch.setattr(attestation, "_split_status", replace_after_capture)
    result = attestation.verify_resume_status(
        status, state_id=_STATE, key=_KEY, profile="release",
        max_files_per_batch=16, workers=1, now=1001,
    )
    assert result.ok, result.reason
    assert observations and all(content == captured for content in observations)
    assert status.read_text(encoding="utf-8") == "preflights PASS 999\n"


def test_prior_status_archive_is_content_addressed_exclusive_and_not_sha_relabelled(
    tmp_path: Path,
) -> None:
    status, archive = tmp_path / ".gate-status", tmp_path / ".gate-logs"
    assert attestation.archive_prior_status(status, archive) is None
    assert not archive.exists()
    _resume_status(status)
    original = status.read_bytes()
    saved = attestation.archive_prior_status(status, archive)
    assert saved is not None
    assert saved.name == f"gate-resume-prior-{hashlib.sha256(original).hexdigest()}.status"
    assert saved.read_bytes() == original
    identity = saved.stat()
    assert attestation.archive_prior_status(status, archive) == saved
    assert saved.stat().st_ino == identity.st_ino
    status.write_bytes(b"historical malformed status\xff\n")
    newer = attestation.archive_prior_status(status, archive)
    assert newer is not None and newer != saved
    assert saved.read_bytes() == original
    assert newer.read_bytes() == status.read_bytes()
    saved.write_bytes(b"occupied foreign bytes\n")
    status.write_bytes(original)
    with pytest.raises(ValueError, match=r"archive|occupied|identity"):
        attestation.archive_prior_status(status, archive)
    assert saved.read_bytes() == b"occupied foreign bytes\n"


def test_prior_status_archive_refuses_links_and_never_changes_the_target(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.write_bytes(b"outside history\n")
    status = tmp_path / ".gate-status"
    status.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        attestation.archive_prior_status(status, tmp_path / ".gate-logs")
    assert target.read_bytes() == b"outside history\n"
    assert not (tmp_path / ".gate-logs").exists()


def test_signed_current_status_is_accepted(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    now = int(time.time())

    sign_status(status, state_id=_STATE, key=_KEY, now=now)

    result = verify_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=now + 1,
        freshness_seconds=60,
    )
    assert result.ok
    assert result.age_seconds == 1


def test_handwritten_pass_without_attestation_is_rejected(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)

    result = verify_status(status, state_id=_STATE, key=_KEY)

    assert not result.ok
    assert "attestation" in result.reason.lower()


@pytest.mark.parametrize(
    ("mutation", "reason_fragment"),
    (
        ("status", "status digest"),
        ("state", "repository state"),
        ("signature", "signature"),
    ),
)
def test_tampering_is_rejected(
    tmp_path: Path,
    mutation: str,
    reason_fragment: str,
) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    now = int(time.time())
    sign_status(status, state_id=_STATE, key=_KEY, now=now)
    state = _STATE
    if mutation == "status":
        status.write_text(
            status.read_text(encoding="utf-8").replace("smoke PASS", "smoke FAIL"),
            encoding="utf-8",
        )
    elif mutation == "state":
        state = "state-" + "b" * 64
    else:
        status.write_text(
            status.read_text(encoding="utf-8").replace(
                "attestation-signature ",
                "attestation-signature 0",
            ),
            encoding="utf-8",
        )

    result = verify_status(
        status,
        state_id=state,
        key=_KEY,
        now=now,
        freshness_seconds=60,
    )

    assert not result.ok
    assert reason_fragment in result.reason.lower()


def test_stale_and_future_attestations_are_rejected(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    sign_status(status, state_id=_STATE, key=_KEY, now=1_000)

    stale = verify_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=1_061,
        freshness_seconds=60,
    )
    future = verify_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=900,
        freshness_seconds=60,
    )

    assert not stale.ok and "stale" in stale.reason.lower()
    assert not future.ok and "future" in future.reason.lower()


def test_failed_or_incomplete_gate_cannot_be_signed(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    status.write_text(
        status.read_text(encoding="utf-8").replace(
            "=== GATE: PASSED ===",
            "=== GATE: FAILED ===",
        ).replace("lint PASS 0", "preflights FAIL 1"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="passed"):
        sign_status(status, state_id=_STATE, key=_KEY)


def test_failed_gate_can_be_authenticated_without_becoming_green(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    status.write_text(
        status.read_text(encoding="utf-8").replace(
            "=== GATE: PASSED ===",
            "=== GATE: FAILED ===",
        ).replace("lint PASS 0", "preflights FAIL 1"),
        encoding="utf-8",
    )

    sign_terminal_status(status, state_id=_STATE, key=_KEY, now=1_000)
    terminal = verify_terminal_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=1_001,
        freshness_seconds=60,
    )
    result = verify_status(
        status,
        state_id=_STATE,
        key=_KEY,
        now=1_001,
        freshness_seconds=60,
    )

    assert terminal.ok
    assert not result.ok
    assert "uniquely completed passed gate" in result.reason
    assert "attestation-signature" in status.read_text(encoding="utf-8")


def test_duplicate_attestation_field_is_rejected(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    _passed_status(status)
    sign_status(status, state_id=_STATE, key=_KEY)
    status.write_text(
        status.read_text(encoding="utf-8") + "attestation-version 1\n",
        encoding="utf-8",
    )

    result = verify_status(status, state_id=_STATE, key=_KEY)

    assert not result.ok
    assert "duplicate" in result.reason.lower()


def test_worktree_and_index_state_must_converge_before_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-q")
    git("config", "user.email", "gate-test@example.invalid")
    git("config", "user.name", "Gate Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-q", "-m", "initial")
    assert repository_state_id(repo) == repository_state_id(repo, source="index")

    tracked.write_text("two\n", encoding="utf-8")
    assert repository_state_id(repo) != repository_state_id(repo, source="index")
    git("add", "tracked.txt")
    assert repository_state_id(repo) == repository_state_id(repo, source="index")

    added = repo / "added.txt"
    added.write_text("new\n", encoding="utf-8")
    assert repository_state_id(repo) != repository_state_id(repo, source="index")
    git("add", "added.txt")
    assert repository_state_id(repo) == repository_state_id(repo, source="index")


def test_uninitialized_submodule_uses_the_pinned_index_gitlink(tmp_path: Path) -> None:
    submodule = tmp_path / "submodule"
    parent = tmp_path / "parent"
    checkout = tmp_path / "checkout"
    submodule.mkdir()
    parent.mkdir()

    def git(repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    for repo in (submodule, parent):
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "gate-test@example.invalid")
        git(repo, "config", "user.name", "Gate Test")

    (submodule / "tracked.txt").write_text("submodule\n", encoding="utf-8")
    git(submodule, "add", "tracked.txt")
    git(submodule, "commit", "-q", "-m", "initial")
    git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(submodule),
        "external/example",
    )
    git(parent, "commit", "-q", "-am", "add submodule")
    subprocess.run(
        ["git", "clone", "-q", "--no-recurse-submodules", str(parent), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (checkout / "external" / "example").is_dir()
    assert repository_state_id(checkout) == repository_state_id(
        checkout,
        source="index",
    )


def test_makefile_signs_final_gate_and_checks_before_commit() -> None:
    makefile = compose_makefile(Path(__file__).parents[2] / "Makefile")

    assert "scripts/gate_status_attestation.py sign .gate-status" in makefile
    assert "scripts/gate_status_attestation.py verify .gate-status" in makefile
    assert "@echo run python scripts/gate_fresh_check.py check" not in makefile
    commit_recipe = makefile.split("\ngit-commit:", 1)[1].split("\n\n", 1)[0]
    assert "check-gate-fresh" in commit_recipe


def test_cli_sign_and_verify_use_one_private_key_and_exact_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = tmp_path / ".gate-status"
    key_path = tmp_path / "keys" / "gate.key"
    _passed_status(status)
    monkeypatch.setattr(attestation, "repository_state_id", lambda *_args, **_kwargs: _STATE)

    common = [str(status), "--repo-root", str(tmp_path), "--key-path", str(key_path)]
    assert attestation.main(["sign", *common]) == 0
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert attestation.main(["verify", *common, "--freshness-seconds", "60"]) == 0

    output = capsys.readouterr().out
    assert "gate attestation signed" in output
    assert "gate attestation valid" in output


def test_cli_authenticates_terminal_failure_without_admitting_green(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = tmp_path / ".gate-status"
    key_path = tmp_path / "gate.key"
    _passed_status(status)
    status.write_text(
        status.read_text(encoding="utf-8")
        .replace("lint PASS 0", "preflights FAIL 1")
        .replace("=== GATE: PASSED ===", "=== GATE: FAILED ==="),
        encoding="utf-8",
    )
    monkeypatch.setattr(attestation, "repository_state_id", lambda *_args, **_kwargs: _STATE)
    common = [str(status), "--repo-root", str(tmp_path), "--key-path", str(key_path)]

    assert attestation.main(["sign-terminal", *common]) == 0
    assert attestation.main(["verify", *common]) == 1
    captured = capsys.readouterr()
    assert "terminal gate attestation signed" in captured.out
    assert "uniquely completed passed gate" in captured.err


def test_cli_fails_closed_on_index_drift_and_invalid_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = tmp_path / ".gate-status"
    key_path = tmp_path / "gate.key"
    _passed_status(status)
    key_path.write_text(_KEY.hex(), encoding="ascii")
    sign_status(status, state_id=_STATE, key=_KEY)
    monkeypatch.setattr(
        attestation,
        "repository_state_id",
        lambda *_args, source="worktree": _STATE if source == "worktree" else "other",
    )
    common = [str(status), "--repo-root", str(tmp_path), "--key-path", str(key_path)]

    assert attestation.main(["verify", *common]) == 1
    assert "staged index does not match" in capsys.readouterr().err
    key_path.write_text("00", encoding="ascii")
    assert attestation.main(["verify", *common]) == 2
    assert "must contain 32 bytes" in capsys.readouterr().err


def test_verifier_rejects_missing_version_and_epoch_evidence(tmp_path: Path) -> None:
    missing = verify_status(tmp_path / "missing", state_id=_STATE, key=_KEY)
    assert not missing.ok and "missing" in missing.reason

    status = tmp_path / ".gate-status"
    _passed_status(status)
    sign_status(status, state_id=_STATE, key=_KEY, now=1_000)
    signed = status.read_text(encoding="utf-8")
    status.write_text(
        signed.replace("attestation-version 1", "attestation-version 2"),
        encoding="utf-8",
    )
    version = verify_status(status, state_id=_STATE, key=_KEY, now=1_001)
    assert not version.ok and "version" in version.reason

    status.write_text(
        signed.replace("attestation-epoch 1000", "attestation-epoch invalid"),
        encoding="utf-8",
    )
    epoch = verify_status(status, state_id=_STATE, key=_KEY, now=1_001)
    assert not epoch.ok and "integer" in epoch.reason


def test_signers_reject_malformed_or_incomplete_terminal_bodies(tmp_path: Path) -> None:
    status = tmp_path / ".gate-status"
    status.write_text("attestation-unknown value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        sign_status(status, state_id=_STATE, key=_KEY)

    _passed_status(status)
    status.write_text(
        status.read_text(encoding="utf-8").replace("smoke PASS", "smoke PENDING"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="required gate phase"):
        sign_status(status, state_id=_STATE, key=_KEY)

    status.write_text("=== GATE: FAILED ===\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no failed phase"):
        sign_terminal_status(status, state_id=_STATE, key=_KEY)


def test_git_helpers_reject_command_and_merge_stage_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        attestation.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"fatal",
        ),
    )
    with pytest.raises(RuntimeError, match="fatal"):
        attestation._run_git(tmp_path, "status")

    monkeypatch.setattr(
        attestation,
        "_run_git",
        lambda *_args: b"100644 " + b"a" * 40 + b" 2\tconflict.py\0",
    )
    with pytest.raises(RuntimeError, match="unresolved merge"):
        attestation._index_entries(tmp_path)


@pytest.fixture(params=("sha1", "sha256"))
def native_metadata_repository(
    tmp_path: Path, request: pytest.FixtureRequest,
) -> tuple[Path, str, str]:
    """Use actual Git objects in an isolated repository, including a spaced root."""
    object_format = request.param
    assert isinstance(object_format, str)
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    repo = repo.resolve()
    for arguments in (
        ("init", "-q", f"--object-format={object_format}"),
        ("config", "user.email", "gate-test@example.invalid"),
        ("config", "user.name", "Gate Test"),
    ):
        attestation._run_git(repo, *arguments)
    (repo / "tracked.txt").write_bytes(b"one\n")
    (repo / "SESSION.md").write_bytes(b"bookkeeping\n")
    attestation._run_git(repo, "add", "tracked.txt", "SESSION.md")
    attestation._run_git(repo, "commit", "-q", "-m", "initial")
    head = attestation._run_git(repo, "rev-parse", "HEAD").decode("ascii").strip()
    return repo, object_format, head


@pytest.mark.parametrize("source", ("worktree", "index"))
def test_repository_identity_batches_only_fresh_metadata_with_equivalent_digests(
    native_metadata_repository: tuple[Path, str, str],
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All content reads remain, while three adjacent metadata queries become one."""
    repo, object_format, head = native_metadata_repository
    nested = repo / "nested"
    nested.mkdir()
    real_run_git = attestation._run_git
    calls: list[tuple[Path, tuple[str, ...]]] = []

    def counted_git(root: Path, *arguments: str) -> bytes:
        calls.append((root, arguments))
        return real_run_git(root, *arguments)

    monkeypatch.setattr(attestation, "_run_git", counted_git)
    for include_head in (True, False):
        for exclude_bookkeeping in (False, True):
            entries = {
                path: ("100644", attestation._git_blob_id(data, object_format))
                for path, data in (
                    ("tracked.txt", b"one\n"),
                    ("SESSION.md", b"bookkeeping\n"),
                )
                if not exclude_bookkeeping or path != "SESSION.md"
            }
            expected = attestation._state_digest(
                head if include_head else "", entries, object_format=object_format,
            )
            calls.clear()
            actual = attestation.repository_state_id(
                nested, source=source, include_head=include_head,
                exclude_bookkeeping=exclude_bookkeeping,
            )
            assert actual == expected
            assert len(calls) == 2, calls
            assert calls[0] == (
                nested,
                ("rev-parse", "--show-toplevel", "--show-object-format", "HEAD"),
            )
            assert calls[1] == (
                repo,
                ("ls-files", "--cached", "--others", "--exclude-standard", "--stage", "-t", "-z")
                if source == "worktree" else ("ls-files", "--stage", "-z"),
            )


def test_batched_metadata_does_not_survive_content_index_or_head_mutations(
    native_metadata_repository: tuple[Path, str, str],
) -> None:
    """Independent source/check boundaries must always obtain current identities."""
    repo, _object_format, _head = native_metadata_repository
    before = repository_state_id(repo)
    assert before == repository_state_id(repo, source="index")
    tracked = repo / "tracked.txt"
    tracked.write_bytes(b"changed\n")
    assert repository_state_id(repo) != before
    assert repository_state_id(repo, source="index") == before

    attestation._run_git(repo, "add", "tracked.txt")
    staged = repository_state_id(repo)
    assert staged == repository_state_id(repo, source="index")
    assert staged != before
    attestation._run_git(repo, "commit", "-q", "-m", "changed")
    committed = repository_state_id(repo)
    assert committed == repository_state_id(repo, source="index")
    assert committed != staged

    without_head = repository_state_id(repo, include_head=False)
    attestation._run_git(repo, "commit", "--allow-empty", "-q", "-m", "head only")
    current = repository_state_id(repo)
    assert current != committed
    assert repository_state_id(repo, include_head=False) == without_head
    assert repository_state_id(repo, source="index") == current

    tracked.unlink()
    assert repository_state_id(repo) != current
    assert repository_state_id(repo, source="index") == current
    tracked.write_bytes(b"changed\n")
    assert repository_state_id(repo) == current
    (repo / "untracked.txt").write_bytes(b"new\n")
    assert repository_state_id(repo) != current
    assert repository_state_id(repo, source="index") == current


def test_batched_metadata_preserves_newlines_and_unicode_in_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse the fixed metadata suffix without splitting a valid root pathname."""
    root = tmp_path / "path with space\nline-\u03bb"
    head = "a" * 40
    metadata = os.fsencode(root) + b"\nsha1\n" + head.encode("ascii") + b"\n"
    calls: list[tuple[Path, tuple[str, ...]]] = []

    def native_git(repo: Path, *arguments: str) -> bytes:
        calls.append((repo, arguments))
        if arguments == ("rev-parse", "--show-toplevel", "--show-object-format", "HEAD"):
            return metadata
        assert repo == root
        assert arguments == ("ls-files", "--stage", "-z")
        return b""

    monkeypatch.setattr(attestation, "_run_git", native_git)
    assert repository_state_id(tmp_path, source="index") == attestation._state_digest(
        head, {}, object_format="sha1",
    )
    assert len(calls) == 2


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-field", "missing-terminator", "extra-tail", "unknown-format",
        "non-ascii", "wrong-head-width", "nonhex-head", "relative-root",
    ),
)
def test_batched_metadata_refuses_malformed_identity_before_content_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    root = os.fsencode(tmp_path)
    object_format = b"sha1"
    head = b"a" * 40
    if mutation == "unknown-format":
        object_format = b"sha512"
    elif mutation == "non-ascii":
        object_format = b"\xff"
    elif mutation == "wrong-head-width":
        head = b"a" * 39
    elif mutation == "nonhex-head":
        head = b"z" * 40
    elif mutation == "relative-root":
        root = b"relative-root"
    metadata = root + b"\n" + object_format + b"\n" + head + b"\n"
    if mutation == "missing-field":
        metadata = root + b"\n" + head + b"\n"
    elif mutation == "missing-terminator":
        metadata = metadata[:-1]
    elif mutation == "extra-tail":
        metadata += b"unexpected"

    def malformed_git(_repo: Path, *arguments: str) -> bytes:
        assert arguments == ("rev-parse", "--show-toplevel", "--show-object-format", "HEAD")
        return metadata

    monkeypatch.setattr(attestation, "_run_git", malformed_git)
    with pytest.raises(RuntimeError, match="malformed Git repository identity"):
        repository_state_id(tmp_path, source="index")


def test_batched_metadata_keeps_native_git_failure_for_an_unborn_head(tmp_path: Path) -> None:
    repo = tmp_path / "unborn"
    repo.mkdir()
    attestation._run_git(repo, "init", "-q")
    with pytest.raises(RuntimeError, match=r"git rev-parse .* failed"):
        repository_state_id(repo, source="index")


def _legacy_worktree_state(
    repo: Path, *, include_head: bool = True, exclude_bookkeeping: bool = False,
) -> str:
    """Frozen pre-fusion byte oracle: separate stage/path queries and direct hashes."""
    raw_root, raw_format, raw_head, tail = attestation._run_git(
        repo, "rev-parse", "--show-toplevel", "--show-object-format", "HEAD",
    ).rsplit(b"\n", 3)
    assert tail == b""
    root = Path(os.fsdecode(raw_root).strip())
    object_format = raw_format.decode("ascii").strip()
    head = raw_head.decode("ascii").strip() if include_head else ""
    index: dict[str, tuple[str, str]] = {}
    for record in attestation._run_git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise RuntimeError("cannot attest an index with unresolved merge entries")
        index[os.fsdecode(raw_path)] = (mode, object_id)
    raw_paths = attestation._run_git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    entries: dict[str, tuple[str, str]] = {}
    for raw_path in raw_paths.split(b"\0"):
        if not raw_path:
            continue
        path = os.fsdecode(raw_path)
        if exclude_bookkeeping and path in {"TASKS.md", "BUGS.md", "SESSION.md"}:
            continue
        full_path = root / path
        indexed_mode, indexed_object_id = index.get(path, ("", ""))
        if indexed_mode == "160000":
            object_id = indexed_object_id
            if full_path.is_dir() and (full_path / ".git").exists():
                object_id = attestation._run_git(full_path, "rev-parse", "HEAD").decode("ascii").strip()
            entries[path] = (indexed_mode, object_id)
            continue
        if not full_path.exists() and not full_path.is_symlink():
            continue
        if full_path.is_symlink():
            data = os.fsencode(os.readlink(full_path))
            mode = "120000"
        elif full_path.is_file():
            data = full_path.read_bytes()
            if indexed_mode in {"100644", "100755"}:
                mode = indexed_mode
            else:
                mode = "100755" if full_path.stat().st_mode & 0o100 else "100644"
        else:
            raise RuntimeError(f"unsupported repository entry: {path}")
        blob = hashlib.new(object_format)
        blob.update(f"blob {len(data)}\0".encode())
        blob.update(data)
        entries[path] = (mode, blob.hexdigest())
    digest = hashlib.sha256()
    digest.update(b"gludd-gate-state-v1\0")
    digest.update(head.encode("ascii") + b"\0" + object_format.encode("ascii") + b"\0")
    for path in sorted(entries, key=os.fsencode):
        mode, object_id = entries[path]
        digest.update(os.fsencode(path) + b"\0" + mode.encode("ascii") + b"\0" + object_id.encode("ascii") + b"\0")
    return digest.hexdigest()


def _assert_legacy_options(repo: Path) -> None:
    for include_head in (True, False):
        for exclude_bookkeeping in (False, True):
            assert repository_state_id(
                repo, include_head=include_head, exclude_bookkeeping=exclude_bookkeeping,
            ) == _legacy_worktree_state(
                repo, include_head=include_head, exclude_bookkeeping=exclude_bookkeeping,
            )


def _native_index_input(repo: Path, data: bytes) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "update-index", "-z", "--index-info"],
        input=data, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_worktree_fusion_matches_legacy_bytes_for_mixed_native_inventory(
    native_metadata_repository: tuple[Path, str, str],
) -> None:
    repo, _object_format, _head = native_metadata_repository
    _assert_legacy_options(repo)
    (repo / ".gitignore").write_text("ignored-*\n", encoding="utf-8")
    (repo / "deleted").write_bytes(b"deleted")
    executable = repo / "executable"
    executable.write_bytes(b"run\0entire file")
    executable.chmod(0o755)
    (repo / "tracked-link").symlink_to("tracked.txt")
    attestation._run_git(repo, "add", "--", ".gitignore", "deleted", "executable", "tracked-link")
    attestation._run_git(repo, "commit", "-q", "-m", "mixed fixture")
    attestation._run_git(repo, "update-index", "--skip-worktree", "executable")
    attestation._run_git(repo, "update-index", "--assume-unchanged", "tracked.txt")
    (repo / "tracked.txt").write_bytes(b"dirty\0current full bytes\n")
    (repo / "deleted").unlink()
    (repo / "SESSION.md").write_bytes(b"changed bookkeeping")
    for name in (
        b"100644 " + b"a" * 40 + b" 0\tstage-looking",
        b"H 100755 " + b"b" * 64 + b" 0\tH-looking",
        b"? another", b"S prefix", b"tab\tname", b"line\nname", b"unicode-\xce\xbb",
    ):
        (repo / os.fsdecode(name)).write_bytes(b"untracked\0full bytes")
    (repo / "untracked-link").symlink_to("missing-target")
    (repo / "ignored-hidden").write_bytes(b"must remain excluded")
    _assert_legacy_options(repo)


@pytest.mark.parametrize("tag", (b"H", b"S", b"?"))
def test_worktree_inventory_raw_non_utf8_paths_are_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tag: bytes,
) -> None:
    raw_path = b"H 100644 " + b"f" * 64 + b" 0\tpath-\xff\nline"
    row = tag + b" " + (
        raw_path if tag == b"?" else b"100644 " + b"a" * 40 + b" 0\t" + raw_path
    ) + b"\0"
    calls: list[tuple[str, ...]] = []

    def inventory(_repo: Path, *arguments: str) -> bytes:
        calls.append(arguments)
        return row

    monkeypatch.setattr(attestation, "_run_git", inventory)
    index, paths = attestation._worktree_inventory(tmp_path)
    assert [os.fsencode(path) for path in paths] == [raw_path]
    assert index == ({} if tag == b"?" else {os.fsdecode(raw_path): ("100644", "a" * 40)})
    assert calls == [("ls-files", "--cached", "--others", "--exclude-standard", "--stage", "-t", "-z")]


def test_native_non_utf8_index_preserves_old_digest_or_native_error(
    native_metadata_repository: tuple[Path, str, str],
) -> None:
    repo, _object_format, _head = native_metadata_repository
    object_id = attestation._run_git(repo, "rev-parse", "HEAD:tracked.txt").strip()
    _native_index_input(repo, b"100644 " + object_id + b"\tindex-\xff\0")
    try:
        expected = _legacy_worktree_state(repo)
    except OSError as original:
        with pytest.raises(type(original)) as current:
            repository_state_id(repo)
        assert current.value.errno == original.errno
        assert current.value.filename == original.filename
    else:
        assert repository_state_id(repo) == expected
    staged = attestation._index_entries(repo)
    assert os.fsdecode(b"index-\xff") in staged


def test_worktree_fusion_preserves_initialized_gitlink_and_fresh_child_head(
    native_metadata_repository: tuple[Path, str, str],
) -> None:
    repo, object_format, _head = native_metadata_repository
    child = repo / "child"
    child.mkdir()
    for arguments in (
        ("init", "-q", f"--object-format={object_format}"),
        ("config", "user.email", "gitlink@example.invalid"),
        ("config", "user.name", "Gitlink"),
    ):
        attestation._run_git(child, *arguments)
    (child / "content").write_bytes(b"child")
    attestation._run_git(child, "add", "content")
    attestation._run_git(child, "commit", "-q", "-m", "child initial")
    child_head = attestation._run_git(child, "rev-parse", "HEAD").decode("ascii").strip()
    attestation._run_git(repo, "update-index", "--add", "--cacheinfo", f"160000,{child_head},child")
    _assert_legacy_options(repo)
    before = repository_state_id(repo)
    attestation._run_git(child, "commit", "--allow-empty", "-q", "-m", "child changed")
    _assert_legacy_options(repo)
    assert repository_state_id(repo) != before
    assert repository_state_id(repo, source="index") != repository_state_id(repo)


def test_worktree_fusion_preserves_native_unmerged_refusal(
    native_metadata_repository: tuple[Path, str, str],
) -> None:
    repo, _object_format, _head = native_metadata_repository
    object_id = attestation._run_git(repo, "rev-parse", "HEAD:tracked.txt").strip()
    _native_index_input(
        repo, b"".join(
            b"100644 " + object_id + b" " + str(stage).encode("ascii") + b"\tconflict\0"
            for stage in (1, 2, 3)
        ),
    )
    with pytest.raises(RuntimeError, match="unresolved merge") as original:
        _legacy_worktree_state(repo)
    for source in ("worktree", "index"):
        with pytest.raises(RuntimeError) as current:
            repository_state_id(repo, source=source)
        assert str(current.value) == str(original.value)


@pytest.mark.parametrize(
    ("invalid_row", "expected_error"),
    (
        (b"Q name\0", RuntimeError),
        (b"Hmissing-separator\0", RuntimeError),
        (b"? \0", RuntimeError),
        (b"? same\0? same\0", RuntimeError),
        (b"? missing-terminator", RuntimeError),
        (b"H no-stage-fields\0", ValueError),
        (b"H 100644 " + b"a" * 40 + b" 2\tconflict\0", RuntimeError),
        (b"M 100644 " + b"a" * 40 + b" 0\tconflict\0", RuntimeError),
        (b"H 100644 \xff 0\tpath\0", UnicodeDecodeError),
    ),
)
def test_worktree_fusion_refuses_invalid_producer_before_body_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    invalid_row: bytes, expected_error: type[Exception],
) -> None:
    read_paths: list[Path] = []
    metadata = os.fsencode(tmp_path) + b"\nsha1\n" + b"a" * 40 + b"\n"

    def producer(_repo: Path, *arguments: str) -> bytes:
        return metadata if arguments[0] == "rev-parse" else invalid_row

    def body(path: Path) -> bytes:
        read_paths.append(path)
        return b"body"

    monkeypatch.setattr(attestation, "_run_git", producer)
    monkeypatch.setattr(Path, "read_bytes", body)
    with pytest.raises(expected_error):
        repository_state_id(tmp_path)
    assert read_paths == []


def test_worktree_fusion_preserves_native_inventory_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = os.fsencode(tmp_path) + b"\nsha1\n" + b"a" * 40 + b"\n"
    commands: list[list[str]] = []

    def failed_inventory(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        if "rev-parse" in command:
            return SimpleNamespace(returncode=0, stdout=metadata, stderr=b"")
        return SimpleNamespace(returncode=17, stdout=b"", stderr=b"native inventory fatal")

    monkeypatch.setattr(attestation.subprocess, "run", failed_inventory)
    with pytest.raises(RuntimeError, match="native inventory fatal"):
        repository_state_id(tmp_path)
    assert len(commands) == 2
    assert commands[-1][3:] == [
        "ls-files", "--cached", "--others", "--exclude-standard", "--stage", "-t", "-z",
    ]


def test_unknown_source_keeps_metadata_and_index_error_order(
    native_metadata_repository: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _object_format, _head = native_metadata_repository
    actual_git = attestation._run_git
    calls: list[tuple[str, ...]] = []

    def observe(root: Path, *arguments: str) -> bytes:
        calls.append(arguments)
        return actual_git(root, *arguments)

    monkeypatch.setattr(attestation, "_run_git", observe)
    with pytest.raises(ValueError, match="unknown repository state source: unknown"):
        repository_state_id(repo, source="unknown")
    expected_queries = [
        ("rev-parse", "--show-toplevel", "--show-object-format", "HEAD"),
        ("ls-files", "--stage", "-z"),
    ]
    assert calls == expected_queries
    object_id = actual_git(repo, "rev-parse", "HEAD:tracked.txt").strip()
    _native_index_input(repo, b"100644 " + object_id + b" 2\tconflict\0")
    calls.clear()
    with pytest.raises(RuntimeError, match="unresolved merge"):
        repository_state_id(repo, source="unknown")
    assert calls == expected_queries
