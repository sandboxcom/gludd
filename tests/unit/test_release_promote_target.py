"""Executable contract tests for exact-SHA release promotion."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from scripts.check_make_target_contract import _stanzas
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
CONTRACT = ROOT / "config" / "make_target_contract.json"


def _target_block(name: str) -> str:
    content = compose_makefile(MAKEFILE)
    marker = f"\n{name}:"
    start = content.index(marker)
    end = content.find("\n\n", start)
    return content[start : len(content) if end == -1 else end]


def _run_promote(*variables: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "release-promote", *variables],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_real_promotion_orders_evidence_before_the_only_ref_mutation() -> None:
    """Evidence and readiness must pass before master can move."""
    block = _target_block("release-promote")

    dual_track = block.index('DUAL_TRACK_CI_VALIDATE_ONLY=0')
    readiness = block.index('RELEASE_READINESS_VALIDATE_ONLY=0')
    merge = block.index('merge --ff-only "$$DEV_SHA"')
    publication = block.index('release-cut TAG="$(TAG)"')

    assert dual_track < readiness < merge < publication


def test_promotion_is_confined_to_the_canonical_main_checkout() -> None:
    """The development worktree may orchestrate but cannot own master."""
    block = _target_block("release-promote")

    assert "MAIN_PATH='/Users/shawnwilson/gludd'" in block
    assert '[ "$$CURRENT_SHA" = "$$DEV_SHA" ]' in block
    assert "worktree-guard" in block
    assert "main-worktree-guard" in block
    assert 'git -C "$$MAIN_PATH" merge-base --is-ancestor' in block
    assert 'git -C "$$MAIN_PATH" merge --ff-only "$$DEV_SHA"' in block
    assert 'git -C "$$MAIN_PATH" merge --ff-only development' not in block
    assert 'git -C "$$MAIN_PATH" checkout master' in block
    assert "merge --no-ff" not in block


def test_validation_mode_has_no_publication_or_ref_write_path() -> None:
    """Validate-only proves the policy while leaving refs and remotes untouched."""
    block = _target_block("release-promote")
    validate_start = block.index('if [ "$(RELEASE_PROMOTE_VALIDATE_ONLY)" = "1" ]')
    validate_end = block.index("\tfi;", validate_start)
    validate_block = block[validate_start:validate_end]

    assert "DUAL_TRACK_CI_VALIDATE_ONLY=1" in validate_block
    assert "RELEASE_READINESS_VALIDATE_ONLY=1" in validate_block
    assert "RELEASE-PROMOTE-VALIDATED" in validate_block
    assert "merge --ff-only" not in validate_block
    assert "release-cut" not in validate_block
    assert "git push" not in validate_block


def test_promotion_delegates_publication_instead_of_reimplementing_it() -> None:
    """Tagging, pushing, and artifact polling remain owned by release-cut."""
    block = _target_block("release-promote")

    assert 'release-cut TAG="$(TAG)" MSG="$(MSG)"' in block
    assert "git-tag-push" not in block
    assert "git-push-sandboxcom" not in block
    assert "gh release" not in block


def test_missing_tag_and_invalid_mode_fail_before_topology_checks() -> None:
    """Operator-input failures are fast, deterministic, and side-effect free."""
    missing = _run_promote("RELEASE_PROMOTE_VALIDATE_ONLY=1")
    invalid = _run_promote("TAG=v0.1.0-beta.4", "RELEASE_PROMOTE_VALIDATE_ONLY=maybe")

    assert missing.returncode == 2
    assert "Usage: make release-promote" in missing.stdout + missing.stderr
    assert invalid.returncode == 2
    assert "must be 0 or 1" in invalid.stdout + invalid.stderr


def test_public_help_and_make_contract_include_safe_behavior() -> None:
    """The documented command must be mechanically discoverable and runnable."""
    makefile = compose_makefile(MAKEFILE)
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    entries = {entry["name"]: entry for entry in payload["targets"]}

    assert "release-promote TAG=.. MSG=.." in makefile
    assert makefile.count('@echo "  release-promote') == 1
    assert "release-promote" in entries
    assert entries["release-promote"]["make_variables"] == [
        "TAG",
        "MSG",
        "RELEASE_PROMOTE_VALIDATE_ONLY",
        "REVIEWED_HEAD_INTEGRATION_RECEIPT",
    ]
    assert "RELEASE_PROMOTE_VALIDATE_ONLY=1" in entries["release-promote"]["behavior"]


def test_release_deploy_is_only_a_compatibility_alias() -> None:
    """No public deployment target may bypass the promotion owner."""
    block = _target_block("release-deploy")

    assert 'release-promote TAG="$(TAG)" MSG="$(MSG)"' in block
    assert (
        'REVIEWED_HEAD_INTEGRATION_RECEIPT="$(REVIEWED_HEAD_INTEGRATION_RECEIPT)"'
        in block
    )
    for forbidden in (
        "development-merge-to-master",
        "git-push-sandboxcom",
        "git-tag-push",
        "ci-await",
        "verify-release-artifact",
        "verify-release-completeness",
    ):
        assert forbidden not in block


def test_make_contract_parser_keeps_the_final_target_at_eof() -> None:
    """A public target at EOF must not disappear from static contract checks."""
    makefile = "first:\n\t@echo first\n\nrelease-promote:\n\t@echo promote\n"

    stanzas = _stanzas(makefile)

    assert "release-promote" in stanzas
    assert "@echo promote" in stanzas["release-promote"]


def test_promote_carries_source_bound_evidence_across_fast_forward() -> None:
    """Publication must retain the already-validated development evidence identity."""
    block = _target_block("release-promote")

    evidence = block.index('LOCAL_ATTESTATION=')
    merge = block.index('merge --ff-only "$$DEV_SHA"')
    publication = block.index('release-cut TAG="$(TAG)"')

    assert evidence < merge < publication
    assert 'RELEASE_CANDIDATE_SHA="$$DEV_SHA"' in block
    assert (
        'REVIEWED_HEAD_INTEGRATION_RECEIPT="$(REVIEWED_HEAD_INTEGRATION_RECEIPT)"'
        in block
    )
    assert "$(abspath $(REVIEWED_HEAD_INTEGRATION_RECEIPT))" in block
    assert 'RELEASE_CI_BRANCH=development' in block
    assert 'RELEASE_LOCAL_ATTESTATION="$$LOCAL_ATTESTATION"' in block


def test_release_cut_revalidates_explicit_source_bound_evidence() -> None:
    """The cut must bind its repeated check to the promoted SHA and source evidence."""
    block = _target_block("release-cut")

    assert "RELEASE_CANDIDATE_SHA" in block
    assert "RELEASE_CI_BRANCH" in block
    assert "RELEASE_LOCAL_ATTESTATION" in block
    assert '[ "$$HEAD_SHA" = "$$SHA_TO_VERIFY" ]' in block
    readiness = block.index('release-readiness TAG="$(TAG)"')
    dual_track = block.index("require-dual-track-green")
    assert readiness < dual_track
    assert 'RELEASE_READINESS_VALIDATE_ONLY=1' in block
    assert (
        'REVIEWED_HEAD_INTEGRATION_RECEIPT="$(REVIEWED_HEAD_INTEGRATION_RECEIPT)"'
        in block
    )
    assert 'RELEASE_CANDIDATE_SHA="$$SHA_TO_VERIFY"' in block
    assert 'CI_BRANCH="$(RELEASE_CI_BRANCH)"' in block
    assert 'DUAL_TRACK_CI_LOCAL_ATTESTATION="$(RELEASE_LOCAL_ATTESTATION)"' in block


def test_dual_track_gate_accepts_explicit_branch_and_local_attestation() -> None:
    """Exact evidence lookup must not silently switch namespaces after promotion."""
    ci_block = _target_block("require-ci-green")
    dual_block = _target_block("require-dual-track-green")

    assert '"$(CI_BRANCH)"' in ci_block
    assert 'CI_BRANCH="$(CI_BRANCH)"' in dual_block
    assert '--local-attestation "$(DUAL_TRACK_CI_LOCAL_ATTESTATION)"' in dual_block


FAKE_OWNER = r'''
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["GLUDD_FAKE_STATE"])
state = json.loads(state_path.read_text())
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
canonical = "-C" in args and args[args.index("-C") + 1] == state["canonical"]
with Path(os.environ["GLUDD_FAKE_EVENTS"]).open("a") as stream:
    stream.write(json.dumps({"tool": tool, "args": args, "canonical": canonical,
                             "branch": state["branch"]}) + "\n")
if tool == "make":
    targets = [a for a in args if not a.startswith("-") and "=" not in a
               and a != state["canonical"]]
    target = targets[0]
    if target == state.get("fail"):
        sys.exit(31)
    variables = dict(a.split("=", 1) for a in args if "=" in a)
    if target == "release-readiness" and variables.get("TAG") in ("v0.1.1", "v0.1.2"):
        receipt = Path(variables.get("REVIEWED_HEAD_INTEGRATION_RECEIPT") or "absent")
        if not receipt.is_file():
            sys.exit(41)
        if json.loads(receipt.read_text())["sha"] != variables["RELEASE_CANDIDATE_SHA"]:
            sys.exit(42)
    if target == "release-readiness" and state.get("advance_master"):
        state["master"] = "c" * 40
        state_path.write_text(json.dumps(state))
    if target == "ci-await":
        print("123")
    sys.exit(0)
if tool == "python":
    if state.get("fail") == "namespace":
        sys.exit(32)
    print("gludd-release-test")
    sys.exit(0)
if tool != "git":
    sys.exit(99)
if "-C" in args:
    if not canonical:
        sys.exit(98)
    index = args.index("-C")
    args = args[:index] + args[index + 2:]
command = args[0]
if command == "rev-parse":
    ref = args[-1]
    if ref.startswith("development"):
        value, failure = state["development"], "development-ref"
    elif ref.startswith("master"):
        value, failure = state["master"], "master-ref"
    elif canonical:
        value = state["master"] if state["branch"] == "master" else state["development"]
        failure = "canonical-head"
    else:
        value, failure = state["head"], "current-ref"
    if state.get("fail") == failure:
        sys.exit(32)
    print(value)
elif command == "merge-base":
    if state.get("fail") == "topology":
        sys.exit(32)
elif command == "checkout":
    if state.get("fail") == "checkout":
        sys.exit(32)
    if args != ["checkout", "master"] or not canonical:
        sys.exit(97)
    if not state.get("checkout_noop"):
        state["branch"] = "master"
    state_path.write_text(json.dumps(state))
elif command == "symbolic-ref":
    if state.get("fail") == "branch-verification":
        sys.exit(32)
    print(state["branch"])
elif command == "merge":
    if state.get("fail") == "merge":
        sys.exit(32)
    if args != ["merge", "--ff-only", state["development"]] or not canonical:
        sys.exit(96)
    if state["branch"] == "master":
        state["master"] = state.get("merged_sha", state["development"])
    if state.get("drift_branch"):
        state["branch"] = "development"
    state_path.write_text(json.dumps(state))
else:
    sys.exit(95)
'''


def _run_isolated_recipe(
    tmp_path: Path, target: str = "release-promote", *, validate: bool = False,
    tag: str = "v0.1.2", receipt: str = "valid", namespace: bool = True,
    contract_example: bool = False,
    **state_overrides: object,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    """Run the actual recipe, mapping only its canonical path to harmless owners."""
    real_make = shutil.which("make")
    assert real_make is not None
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    for tool in ("git", "make", "python"):
        path = binary / tool
        path.write_text(f"#!{sys.executable}\n" + dedent(FAKE_OWNER), encoding="utf-8")
        path.chmod(0o700)
    state: dict[str, object] = {
        "canonical": str(canonical), "branch": "development",
        "head": "b" * 40, "development": "b" * 40, "master": "a" * 40,
        **state_overrides,
    }
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    events_file = tmp_path / "events.jsonl"
    receipt_file = tmp_path / "receipt.json"
    if receipt != "absent":
        receipt_file.write_text(
            json.dumps({"sha": "b" * 40 if receipt == "valid" else "a" * 40}),
            encoding="utf-8",
        )
    resources = tmp_path / "resources"
    attestation = resources / "gludd-release-test" / "ci-shards" / "attestation.json"
    attestation.parent.mkdir(parents=True)
    attestation.write_text("{}", encoding="utf-8")
    block = _target_block(target).replace(
        "MAIN_PATH='/Users/shawnwilson/gludd'", f"MAIN_PATH='{canonical}'"
    )
    fixture_makefile = tmp_path / "Makefile"
    fixture_makefile.write_text(
        f"MAKE := {binary / 'make'}\nPYTHON := {binary / 'python'}\n" + block + "\n",
        encoding="utf-8",
    )
    env = {
        "PATH": f"{binary}{os.pathsep}{os.defpath}",
        "GLUDD_FAKE_STATE": str(state_file), "GLUDD_FAKE_EVENTS": str(events_file),
        "GLUDD_PROJECT_NAMESPACE": "gludd-release-test" if namespace else "",
        "GLUDD_RESOURCE_ROOT": str(resources),
    }
    variables = [
            f"TAG={tag}", "MSG=fixture",
            f"RELEASE_PROMOTE_VALIDATE_ONLY={int(validate)}",
            f"REVIEWED_HEAD_INTEGRATION_RECEIPT={receipt_file}",
            "RELEASE_CANDIDATE_SHA=" + "b" * 40, "RELEASE_CI_BRANCH=development",
            f"RELEASE_LOCAL_ATTESTATION={attestation}",
            "RELEASE_AWAIT_TIMEOUT=1", "RELEASE_AWAIT_INTERVAL=1",
    ]
    if contract_example:
        entry = next(
            item for item in json.loads(CONTRACT.read_text())["targets"]
            if item["name"] == target
        )
        variables = shlex.split(entry["behavior"])[2:]
    result = subprocess.run(
        [real_make, "--no-print-directory", target, *variables],
        cwd=tmp_path, env=env, check=False, capture_output=True, text=True, timeout=10,
    )
    events = [json.loads(line) for line in events_file.read_text().splitlines()]
    return result, events


def _make_targets(events: list[dict[str, object]]) -> list[str]:
    targets = []
    for event in events:
        if event["tool"] == "make":
            args = event["args"]
            assert isinstance(args, list)
            targets.append(next(
                arg for arg in args
                if not arg.startswith("-") and "=" not in arg
                and not arg.startswith("/")
            ))
    return targets


def _git_mutations(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        event for event in events if event["tool"] == "git"
        and any(item in event["args"] for item in ("checkout", "merge", "push", "tag"))
    ]


@pytest.mark.parametrize("validate", [True, False])
@pytest.mark.parametrize("failure", [
    "worktree-guard", "main-worktree-guard", "require-dual-track-green", "release-readiness",
])
def test_promotion_child_failures_stop_before_mutation_or_success(
    tmp_path: Path, validate: bool, failure: str,
) -> None:
    result, events = _run_isolated_recipe(tmp_path, validate=validate, fail=failure)
    assert result.returncode != 0
    assert "RELEASE-PROMOTE-VALIDATED" not in result.stdout
    assert _make_targets(events)[-1] == failure
    assert _git_mutations(events) == []
    assert "release-cut" not in _make_targets(events)


def test_validation_runs_evidence_without_ref_or_publication_writes(tmp_path: Path) -> None:
    result, events = _run_isolated_recipe(tmp_path, validate=True)
    assert result.returncode == 0, result.stderr
    assert "RELEASE-PROMOTE-VALIDATED" in result.stdout
    assert _make_targets(events) == [
        "worktree-guard", "main-worktree-guard", "require-dual-track-green", "release-readiness",
    ]
    assert _git_mutations(events) == []


def test_documented_promotion_behavior_is_safe_with_isolated_evidence_owners(
    tmp_path: Path,
) -> None:
    """Execute the literal contract variables without accessing refs or remotes."""
    result, events = _run_isolated_recipe(tmp_path, contract_example=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELEASE-PROMOTE-VALIDATED" in result.stdout
    assert _git_mutations(events) == []
    assert "release-cut" not in _make_targets(events)


def test_real_promotion_selects_and_verifies_canonical_master_before_publication(
    tmp_path: Path,
) -> None:
    result, events = _run_isolated_recipe(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    mutations = _git_mutations(events)
    assert [event["args"][-2:] for event in mutations] == [
        ["checkout", "master"], ["--ff-only", "b" * 40],
    ]
    assert all(event["canonical"] is True for event in mutations)
    assert mutations[1]["branch"] == "master"
    readiness = next(event for event in events if "release-readiness" in event["args"])
    publication = next(event for event in events if "release-cut" in event["args"])
    assert events.index(readiness) < events.index(mutations[0]) < events.index(mutations[1])
    assert events.index(mutations[1]) < events.index(publication)
    assert publication["branch"] == "master"
    assert publication["canonical"] is True
    assert "RELEASE_CANDIDATE_SHA=" + "b" * 40 in publication["args"]
    assert "RELEASE_CI_BRANCH=development" in publication["args"]
    dual_track = next(event for event in events if "require-dual-track-green" in event["args"])
    source_arguments = dual_track["args"]
    publication_arguments = publication["args"]
    assert isinstance(source_arguments, list)
    assert isinstance(publication_arguments, list)
    local_source = next(
        argument.split("=", 1)[1] for argument in source_arguments
        if argument.startswith("DUAL_TRACK_CI_LOCAL_ATTESTATION=")
    )
    assert "RELEASE_LOCAL_ATTESTATION=" + local_source in publication_arguments
    reviewed_source = next(
        argument for argument in readiness["args"]
        if argument.startswith("REVIEWED_HEAD_INTEGRATION_RECEIPT=")
    )
    assert reviewed_source in publication_arguments
    checks = events[events.index(mutations[1]) + 1:events.index(publication)]
    assert any("symbolic-ref" in event["args"] for event in checks)
    assert any("rev-parse" in event["args"] for event in checks)


@pytest.mark.parametrize("failure", [
    "current-ref", "development-ref", "master-ref", "topology", "namespace",
    "checkout", "branch-verification", "canonical-head", "merge", "release-cut",
])
def test_promotion_git_namespace_and_publication_failures_propagate(
    tmp_path: Path, failure: str,
) -> None:
    result, events = _run_isolated_recipe(tmp_path, fail=failure, namespace=False)
    assert result.returncode != 0
    assert "RELEASE-PROMOTE-VALIDATED" not in result.stdout
    if failure != "release-cut":
        assert "release-cut" not in _make_targets(events)


@pytest.mark.parametrize("override", [
    {"checkout_noop": True}, {"merged_sha": "c" * 40},
    {"drift_branch": True}, {"advance_master": True},
])
def test_promotion_verifies_master_identity_before_publication(
    tmp_path: Path, override: dict[str, object],
) -> None:
    result, events = _run_isolated_recipe(tmp_path, **override)
    assert result.returncode != 0
    assert "release-cut" not in _make_targets(events)


@pytest.mark.parametrize("tag", ["v0.1.1", "v0.1.2"])
@pytest.mark.parametrize("receipt", ["absent", "mismatched"])
def test_stable_release_cut_receipt_rejection_stops_all_publication(
    tmp_path: Path, tag: str, receipt: str,
) -> None:
    result, events = _run_isolated_recipe(tmp_path, "release-cut", tag=tag, receipt=receipt)
    assert result.returncode != 0
    assert _make_targets(events) == ["release-readiness"]
    assert _git_mutations(events) == []


@pytest.mark.parametrize("tag", ["v0.1.1", "v0.1.2"])
@pytest.mark.parametrize("failure", ["release-readiness", "require-dual-track-green"])
def test_stable_release_cut_child_failure_is_not_overwritten(
    tmp_path: Path, tag: str, failure: str,
) -> None:
    result, events = _run_isolated_recipe(tmp_path, "release-cut", tag=tag, fail=failure)
    assert result.returncode != 0
    assert _make_targets(events)[-1] == failure
    assert "git-push-sandboxcom" not in _make_targets(events)
    assert "git-tag-push" not in _make_targets(events)


@pytest.mark.parametrize("tag", ["v0.1.1", "v0.1.2"])
def test_stable_release_cut_validates_receipt_and_sha_before_publication(
    tmp_path: Path, tag: str,
) -> None:
    result, events = _run_isolated_recipe(tmp_path, "release-cut", tag=tag)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _make_targets(events)[:4] == [
        "release-readiness", "require-dual-track-green", "check-readme-status",
        "git-push-sandboxcom",
    ]
    readiness = next(event for event in events if "release-readiness" in event["args"])
    assert "RELEASE_CANDIDATE_SHA=" + "b" * 40 in readiness["args"]
    assert "RELEASE_ALLOW_INCOMPLETE_TASKS=0" in readiness["args"]
    assert "RELEASE_ALLOW_INVALID_RECEIPT=0" in readiness["args"]


@pytest.mark.parametrize("failure", [
    "current-ref", "check-readme-status", "git-push-sandboxcom", "git-tag-push",
    "ci-await", "verify-release-artifact", "verify-release-completeness",
])
def test_release_cut_lookup_and_publication_errors_propagate(
    tmp_path: Path, failure: str,
) -> None:
    result, events = _run_isolated_recipe(tmp_path, "release-cut", fail=failure)
    assert result.returncode != 0
    if failure == "current-ref":
        assert _make_targets(events) == []
    else:
        assert _make_targets(events)[-1] == failure
