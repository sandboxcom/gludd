"""Regression tests for mandatory PyInstaller graph-review receipts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import check_pyinstaller_warning_reviews as review_check

_OLD = "1" * 64
_NEW = "a" * 64


def _policy(primary: str, *, alternates: list[str] | None = None) -> dict[str, object]:
    return {
        "platform": "linux",
        "pyinstaller_version": "6.20.0",
        "transitive_warning_sha256_by_architecture": {"aarch64": primary},
        "reviewed_transitive_warning_sha256_alternates_by_architecture": {
            "aarch64": alternates or [],
        },
    }


def _receipt(before: str = _OLD, after: str = _NEW) -> dict[str, object]:
    return {
        "schema_version": 1,
        "platform": "linux",
        "architecture": "aarch64",
        "pyinstaller_version": "6.20.0",
        "before": {
            "warning_sha256": "b" * 64,
            "transitive_sha256": before,
            "transitive_count": 10,
        },
        "after": {
            "warning_sha256": "c" * 64,
            "transitive_sha256": after,
            "transitive_count": 11,
        },
        "delta": {
            "added_count": 2,
            "removed_count": 1,
            "added": ["missing added_a <- dependency (optional)", "missing added_b <- dependency (optional)"],
            "removed": ["missing removed <- dependency (optional)"],
        },
    }


def test_new_digest_requires_complete_exact_receipt(tmp_path: Path) -> None:
    before = _policy(_OLD)
    after = _policy(_NEW)

    errors = review_check.validate_policy_change(before, after, tmp_path)

    assert errors == [f"missing review receipt for aarch64 digest {_NEW}"]


def test_exact_receipt_admits_reviewed_digest_change(tmp_path: Path) -> None:
    receipt = tmp_path / f"aarch64-{_NEW}.json"
    receipt.write_text(json.dumps(_receipt()), encoding="utf-8")

    errors = review_check.validate_policy_change(
        _policy(_OLD),
        _policy(_NEW),
        tmp_path,
    )

    assert errors == []


def test_added_alternate_also_requires_receipt(tmp_path: Path) -> None:
    errors = review_check.validate_policy_change(
        _policy(_OLD),
        _policy(_OLD, alternates=[_NEW]),
        tmp_path,
    )

    assert errors == [f"missing review receipt for aarch64 digest {_NEW}"]


def test_receipt_must_start_from_an_existing_accepted_digest(tmp_path: Path) -> None:
    receipt = tmp_path / f"aarch64-{_NEW}.json"
    receipt.write_text(json.dumps(_receipt(before="d" * 64)), encoding="utf-8")

    errors = review_check.validate_policy_change(
        _policy(_OLD),
        _policy(_NEW),
        tmp_path,
    )

    assert any("before digest is not accepted by the previous policy" in error for error in errors)


def test_receipt_delta_is_complete_sorted_and_arithmetically_consistent(
    tmp_path: Path,
) -> None:
    malformed = _receipt()
    after_graph = malformed["after"]
    assert isinstance(after_graph, dict)
    after_graph["transitive_count"] = 12
    malformed["delta"] = {
        "added_count": 1,
        "removed_count": 0,
        "added": ["z edge", "a edge"],
        "removed": [],
    }
    receipt = tmp_path / f"aarch64-{_NEW}.json"
    receipt.write_text(json.dumps(malformed), encoding="utf-8")

    errors = review_check.validate_policy_change(
        _policy(_OLD),
        _policy(_NEW),
        tmp_path,
    )

    assert any("added_count does not match" in error for error in errors)
    assert any("added edges must be sorted and unique" in error for error in errors)
    assert any("transitive counts do not reconcile" in error for error in errors)


def test_unchanged_accepted_digest_set_needs_no_new_receipt(tmp_path: Path) -> None:
    policy = _policy(_OLD, alternates=[_NEW])

    assert review_check.validate_policy_change(policy, policy, tmp_path) == []


def test_make_gate_runs_review_checker() -> None:
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(encoding="utf-8")

    gate = makefile.split("\ngate:", 1)[1].split("\n\n", 1)[0]
    gate_preflights = makefile.split("GATE_PREFLIGHT_TARGETS :=", 1)[1].split(
        "GATE_PREFLIGHT_STATUS", 1
    )[0]
    assert "check-pyinstaller-warning-reviews" in gate_preflights
    assert "_gate-preflights" in gate
    assert "\ncheck-pyinstaller-warning-reviews:" in makefile


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {
            "transitive_warning_sha256_by_architecture": {"aarch64": "bad"},
            "reviewed_transitive_warning_sha256_alternates_by_architecture": {},
        },
        {
            "transitive_warning_sha256_by_architecture": {"aarch64": _OLD},
            "reviewed_transitive_warning_sha256_alternates_by_architecture": {"aarch64": "bad"},
        },
        {
            "transitive_warning_sha256_by_architecture": {"aarch64": _OLD},
            "reviewed_transitive_warning_sha256_alternates_by_architecture": {"x86_64": []},
        },
    ],
)
def test_malformed_policy_digest_maps_fail_closed(policy: dict[str, object]) -> None:
    with pytest.raises(review_check.ReviewCheckError):
        review_check._accepted(policy)


@pytest.mark.parametrize("payload", ["not json", "[]"])
def test_json_reader_rejects_malformed_or_non_object_root(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "input.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(review_check.ReviewCheckError):
        review_check._read_json(path)


def test_json_reader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(review_check.ReviewCheckError, match="cannot read JSON"):
        review_check._read_json(tmp_path / "missing.json")


def test_receipt_identity_and_graph_fields_fail_closed(tmp_path: Path) -> None:
    receipt = _receipt()
    receipt.update(
        {
            "schema_version": 2,
            "platform": "darwin",
            "architecture": "x86_64",
            "pyinstaller_version": "0",
        }
    )
    receipt["before"] = {
        "warning_sha256": "bad",
        "transitive_sha256": "bad",
        "transitive_count": True,
    }
    receipt["after"] = {
        "warning_sha256": "bad",
        "transitive_sha256": "d" * 64,
        "transitive_count": -1,
    }
    receipt["delta"] = {
        "added_count": 1,
        "removed_count": 1,
        "added": ["same edge"],
        "removed": ["same edge"],
    }
    path = tmp_path / f"aarch64-{_NEW}.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    errors = review_check.validate_policy_change(_policy(_OLD), _policy(_NEW), tmp_path)

    assert any("schema_version" in error for error in errors)
    assert any("architecture" in error for error in errors)
    assert any("platform" in error for error in errors)
    assert any("PyInstaller version" in error for error in errors)
    assert any("warning_sha256 is invalid" in error for error in errors)
    assert any("transitive_count is invalid" in error for error in errors)
    assert any("after digest does not match" in error for error in errors)
    assert any("both added and removed" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("before", {}, "before graph keys"),
        ("after", {}, "after graph keys"),
        ("delta", {}, "delta keys"),
    ],
)
def test_receipt_requires_exact_nested_keys(
    tmp_path: Path,
    field: str,
    value: dict[str, object],
    message: str,
) -> None:
    receipt = _receipt()
    receipt[field] = value
    path = tmp_path / f"aarch64-{_NEW}.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    errors = review_check.validate_policy_change(_policy(_OLD), _policy(_NEW), tmp_path)

    assert any(message in error for error in errors)


def test_receipt_requires_exact_root_keys(tmp_path: Path) -> None:
    receipt = _receipt()
    receipt["unknown"] = True
    path = tmp_path / f"aarch64-{_NEW}.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    errors = review_check.validate_policy_change(_policy(_OLD), _policy(_NEW), tmp_path)

    assert any("root keys" in error for error in errors)


def test_malformed_receipt_json_is_reported(tmp_path: Path) -> None:
    (tmp_path / f"aarch64-{_NEW}.json").write_text("not json", encoding="utf-8")

    errors = review_check.validate_policy_change(_policy(_OLD), _policy(_NEW), tmp_path)

    assert any("cannot read JSON" in error for error in errors)


def test_git_json_handles_missing_malformed_and_valid_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            SimpleNamespace(returncode=1, stdout="", stderr="missing"),
            SimpleNamespace(returncode=0, stdout="not json", stderr=""),
            SimpleNamespace(returncode=0, stdout="[]", stderr=""),
            SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr=""),
        ]
    )
    monkeypatch.setattr(
        "scripts.check_pyinstaller_warning_reviews.subprocess.run",
        lambda *_args, **_kwargs: next(responses),
    )

    assert review_check._git_json("HEAD", Path("policy.json")) is None
    with pytest.raises(review_check.ReviewCheckError, match="cannot parse policy"):
        review_check._git_json("HEAD", Path("policy.json"))
    with pytest.raises(review_check.ReviewCheckError, match="not a JSON object"):
        review_check._git_json("HEAD", Path("policy.json"))
    assert review_check._git_json("HEAD", Path("policy.json")) == {"ok": True}


def test_git_parent_refs_returns_every_merge_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.check_pyinstaller_warning_reviews.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="head first-parent second-parent\n",
            stderr="",
        ),
    )

    assert review_check._git_parent_refs() == ["first-parent", "second-parent"]


def test_historical_policy_uses_worktree_then_parent_then_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {"current": True}
    calls: list[str] = []
    monkeypatch.setattr(review_check, "_git_parent_refs", lambda _ref="HEAD": ["HEAD^"])

    def first(ref: str, _path: Path) -> dict[str, object] | None:
        calls.append(ref)
        return None

    monkeypatch.setattr(review_check, "_git_json", first)
    assert review_check._historical_policy(Path("policy.json"), current) is current
    assert calls == ["HEAD"]

    monkeypatch.setattr(review_check, "_git_json", lambda ref, _path: {"head": True} if ref == "HEAD" else None)
    assert review_check._historical_policy(Path("policy.json"), current) == {"head": True}

    monkeypatch.setattr(review_check, "_git_json", lambda ref, _path: current if ref == "HEAD" else {"parent": True})
    assert review_check._historical_policy(Path("policy.json"), current) == {"parent": True}


def test_historical_policy_unions_every_merge_parent_accepted_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _policy(_NEW, alternates=["b" * 64])
    first_parent = _policy(_OLD)
    second_parent = _policy(_NEW, alternates=["b" * 64])
    policies = {
        "HEAD": current,
        "first-parent": first_parent,
        "second-parent": second_parent,
    }
    monkeypatch.setattr(
        review_check,
        "_git_json",
        lambda ref, _path: policies.get(ref),
    )
    monkeypatch.setattr(
        review_check,
        "_git_parent_refs",
        lambda _ref="HEAD": ["first-parent", "second-parent"],
        raising=False,
    )

    historical = review_check._historical_policy(Path("policy.json"), current)

    assert review_check._accepted(historical) == {
        "aarch64": {_OLD, _NEW, "b" * 64},
    }
    assert review_check.validate_policy_change(historical, current, Path("receipts")) == []


def test_cli_accepts_explicit_before_policy_and_reports_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    receipt_dir = tmp_path / "receipts"
    before_path.write_text(json.dumps(_policy(_OLD)), encoding="utf-8")
    after_path.write_text(json.dumps(_policy(_NEW)), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_pyinstaller_warning_reviews.py",
            "--policy",
            str(after_path),
            "--receipt-dir",
            str(receipt_dir),
            "--before-policy",
            str(before_path),
        ],
    )

    assert review_check.main() == 1
    assert "missing review receipt" in capsys.readouterr().err
    receipt_dir.mkdir()
    (receipt_dir / f"aarch64-{_NEW}.json").write_text(json.dumps(_receipt()), encoding="utf-8")
    assert review_check.main() == 0
    assert "PASS: every newly accepted" in capsys.readouterr().out
