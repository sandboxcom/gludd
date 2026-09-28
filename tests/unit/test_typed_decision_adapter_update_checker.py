"""Hermetic contracts for the typed-decision adapter update checker."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_typed_decision_adapter_update.py"
SCHEMA = ROOT / "docs" / "releases" / "typed-decision-adapter-contract-v1.schema.json"
EXAMPLES = (
    ROOT
    / "docs"
    / "releases"
    / "typed-decision-adapter-contract-v1.examples.json"
)


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_typed_decision_adapter_update",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _bundle() -> dict[str, object]:
    loaded = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _digests(seed: str) -> Iterator[str]:
    index = 0
    while True:
        value = hashlib.sha256(f"{seed}-{index}".encode()).hexdigest()
        yield f"sha256:{value}"
        index += 1


def _replace_sentinel_digests(value: object, digests: Iterator[str]) -> object:
    if isinstance(value, dict):
        return {
            key: _replace_sentinel_digests(item, digests)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_sentinel_digests(item, digests) for item in value]
    if isinstance(value, str) and value.startswith("sha256:"):
        return next(digests)
    return value


def _production_profile(seed: str = "baseline") -> dict[str, object]:
    profiles = _bundle()["profiles"]
    assert isinstance(profiles, list) and isinstance(profiles[0], dict)
    profile = _replace_sentinel_digests(copy.deepcopy(profiles[0]), _digests(seed))
    assert isinstance(profile, dict)
    profile["example_only"] = False
    source = profile["source_pin"]
    identity = profile["identity"]
    assert isinstance(source, dict) and isinstance(identity, dict)
    source["release"] = "v1.0.0"
    source["commit"] = hashlib.sha1(f"{seed}-source".encode()).hexdigest()
    identity["model_revision"] = f"model-{seed}@v1.0.0"
    return profile


def _candidate_update() -> tuple[dict[str, object], dict[str, object]]:
    baseline = _production_profile()
    candidate = copy.deepcopy(baseline)
    source = candidate["source_pin"]
    identity = candidate["identity"]
    assert isinstance(source, dict) and isinstance(identity, dict)
    source["release"] = "v1.0.1"
    source["commit"] = hashlib.sha1(b"candidate-source").hexdigest()
    source["source_archive_digest"] = next(_digests("candidate-archive"))
    source["reviewed_at"] = "2026-09-28"
    identity["fingerprint"] = next(_digests("candidate-fingerprint"))
    identity["backend_protocol_version"] = "observed-2026-09-28"
    identity["server_digest"] = next(_digests("candidate-server"))
    identity["runtime_digest"] = next(_digests("candidate-runtime"))
    return baseline, candidate


def _nested_string(profile: dict[str, object], section: str, field: str) -> str:
    nested = profile[section]
    assert isinstance(nested, dict)
    value = nested[field]
    assert isinstance(value, str)
    return value


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path


def test_canonical_schema_and_examples_are_hermetic_and_non_runnable() -> None:
    report = checker.check_canonical_contract(SCHEMA, EXAMPLES)
    assert report.ok is True
    assert report.status == checker.CheckStatus.VALID_EXAMPLES
    assert report.findings == ()
    assert report.profile_count == 2
    assert report.example_count == 3


def test_schema_validation_rejects_unknown_and_missing_fields() -> None:
    schema = checker.load_json_object(SCHEMA)
    profile = _production_profile()
    profile["unexpected"] = True
    findings = checker.validate_profile(schema, profile)
    assert any("Additional properties" in finding for finding in findings)

    profile = _production_profile()
    del profile["identity"]
    findings = checker.validate_profile(schema, profile)
    assert any("identity" in finding and "required" in finding for finding in findings)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda profile: profile["source_pin"].__setitem__("commit", "0" * 40),
            "source commit is a sentinel",
        ),
        (
            lambda profile: profile["source_pin"].__setitem__(
                "source_archive_digest",
                "sha256:" + "a" * 64,
            ),
            "source_archive_digest is a sentinel",
        ),
        (
            lambda profile: profile["identity"].__setitem__(
                "model_revision",
                "openjev-latest",
            ),
            "model_revision contains a moving alias",
        ),
        (
            lambda profile: profile.__setitem__("example_only", True),
            "candidate profile is example-only",
        ),
        (
            lambda profile: profile.__setitem__("profile_state", "shadow"),
            "candidate profile must remain disabled",
        ),
    ],
)
def test_candidate_pins_and_disabled_state_fail_closed(
    mutate: object,
    expected: str,
) -> None:
    profile = _production_profile()
    assert callable(mutate)
    mutate(profile)
    findings = checker.check_candidate_pins(profile)
    assert expected in findings


def test_every_identity_digest_is_checked_for_sentinels() -> None:
    profile = _production_profile()
    identity = profile["identity"]
    assert isinstance(identity, dict)
    identity["tokenizer_digest"] = "sha256:" + "f" * 64
    assert checker.check_candidate_pins(profile) == (
        "identity.tokenizer_digest is a sentinel digest",
    )


def test_malformed_candidate_sections_and_moving_aliases_fail_closed() -> None:
    malformed = _production_profile()
    malformed["source_pin"] = None
    malformed["identity"] = None
    malformed["transport"] = None
    assert checker.check_candidate_pins(malformed) == (
        "identity must be an object",
        "source_pin must be an object",
        "transport must be an object",
    )

    moving = _production_profile()
    source = moving["source_pin"]
    transport = moving["transport"]
    assert isinstance(source, dict) and isinstance(transport, dict)
    source["repository"] = "http://example.invalid/source"
    source["release"] = "main"
    source["source_archive_digest"] = "sha256:"
    transport["model_id"] = "jev-main"
    assert checker.check_candidate_pins(moving) == (
        "source release contains a moving alias",
        "source repository must use https",
        "source_archive_digest is a sentinel",
        "transport model_id contains a moving alias",
    )


def test_canonical_examples_require_all_shapes_and_disabled_profiles() -> None:
    schema = checker.load_json_object(SCHEMA)
    bundle = _bundle()
    examples = bundle["normalization_examples"]
    assert isinstance(examples, list)
    examples.pop()
    findings = checker.check_example_bundle(schema, bundle)
    assert "normalization examples must cover choice, noul, and score exactly" in findings

    bundle = _bundle()
    profiles = bundle["profiles"]
    assert isinstance(profiles, list) and isinstance(profiles[0], dict)
    profiles[0]["profile_state"] = "shadow"
    findings = checker.check_example_bundle(schema, bundle)
    assert "example profile openjev-0.1-external-example must remain disabled" in findings


def test_example_semantics_reject_unknown_profile_and_admissible_fixture() -> None:
    schema = checker.load_json_object(SCHEMA)
    bundle = _bundle()
    profiles = bundle["profiles"]
    examples = bundle["normalization_examples"]
    assert isinstance(profiles, list) and isinstance(profiles[0], dict)
    assert isinstance(examples, list) and isinstance(examples[0], dict)
    profiles[0]["example_only"] = False
    examples[0]["profile_id"] = "unknown-profile"
    findings = checker.check_example_bundle(schema, bundle)
    assert "example profile openjev-0.1-external-example must be example-only" in findings
    assert "example openjev-choice-normalization references an unknown profile" in findings


def test_compatible_pinned_update_changes_identity_atomically() -> None:
    schema = checker.load_json_object(SCHEMA)
    baseline, candidate = _candidate_update()
    report = checker.check_profile_update(schema, baseline, candidate)
    assert report.ok is True
    assert report.status == checker.CheckStatus.COMPATIBLE_PINNED_UPDATE
    assert report.findings == ()


def test_identical_pinned_profile_is_a_compatible_noop() -> None:
    schema = checker.load_json_object(SCHEMA)
    baseline = _production_profile()
    report = checker.check_profile_update(schema, baseline, copy.deepcopy(baseline))
    assert report.ok is True
    assert report.status == checker.CheckStatus.COMPATIBLE_NOOP
    assert report.findings == ()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda candidate: candidate["source_pin"].__setitem__(
                "repository",
                "https://example.invalid/fork",
            ),
            "source repository changed",
        ),
        (
            lambda candidate: candidate["source_pin"].__setitem__(
                "commit",
                _nested_string(_production_profile(), "source_pin", "commit"),
            ),
            "source commit and archive digest must change together",
        ),
        (
            lambda candidate: candidate["identity"].__setitem__(
                "fingerprint",
                _nested_string(_production_profile(), "identity", "fingerprint"),
            ),
            "identity-bound inputs changed without a new fingerprint",
        ),
        (
            lambda candidate: candidate["transport"].__setitem__(
                "tls_required",
                False,
            ),
            "transport contract changed",
        ),
        (
            lambda candidate: candidate["capabilities"].__setitem__(
                "max_choice_options",
                19,
            ),
            "capability contract changed",
        ),
        (
            lambda candidate: candidate["redaction"].__setitem__(
                "sanitizer_digest",
                next(_digests("new-sanitizer")),
            ),
            "redaction contract changed",
        ),
        (
            lambda candidate: candidate["source_pin"].__setitem__(
                "reviewed_at",
                "2026-09-26",
            ),
            "review date moved backward",
        ),
    ],
)
def test_incompatible_updates_fail_closed(mutation: object, expected: str) -> None:
    schema = checker.load_json_object(SCHEMA)
    baseline, candidate = _candidate_update()
    assert callable(mutation)
    mutation(candidate)
    report = checker.check_profile_update(schema, baseline, candidate)
    assert report.ok is False
    assert report.status == checker.CheckStatus.INCOMPATIBLE
    assert expected in report.findings


def test_profile_id_contract_and_update_policy_are_immutable() -> None:
    schema = checker.load_json_object(SCHEMA)
    baseline, candidate = _candidate_update()
    candidate["profile_id"] = "different-profile"
    policy = candidate["update_policy"]
    assert isinstance(policy, dict)
    policy["check_cadence_days"] = 7
    report = checker.check_profile_update(schema, baseline, candidate)
    assert report.findings == (
        "profile_id changed",
        "update policy changed",
    )


def test_license_identity_contract_and_fingerprint_only_changes_are_rejected() -> None:
    schema = checker.load_json_object(SCHEMA)
    baseline, candidate = _candidate_update()
    source = candidate["source_pin"]
    identity = candidate["identity"]
    assert isinstance(source, dict) and isinstance(identity, dict)
    source["license"] = "MIT"
    identity["endpoint_trust"] = "attested_local"
    report = checker.check_profile_update(schema, baseline, candidate)
    assert "source license changed" in report.findings
    assert "identity contract changed" in report.findings

    fingerprint_only = copy.deepcopy(baseline)
    fingerprint_identity = fingerprint_only["identity"]
    assert isinstance(fingerprint_identity, dict)
    fingerprint_identity["fingerprint"] = next(_digests("fingerprint-only"))
    report = checker.check_profile_update(schema, baseline, fingerprint_only)
    assert report.findings == (
        "fingerprint changed without identity-bound inputs",
    )


def test_review_date_parser_fails_closed_for_wrong_type_and_invalid_date() -> None:
    assert checker._review_date(None) is None
    assert checker._review_date("not-a-date") is None


def test_invalid_schema_and_profile_are_reported_not_raised(tmp_path: Path) -> None:
    bad_schema = _write_json(tmp_path / "schema.json", {"type": 7})
    examples = _write_json(tmp_path / "examples.json", {})
    report = checker.check_canonical_contract(bad_schema, examples)
    assert report.ok is False
    assert report.status == checker.CheckStatus.INVALID
    assert any("invalid JSON Schema" in finding for finding in report.findings)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"[]\n", "must contain a JSON object"),
        (b"{not-json}\n", "invalid JSON"),
        (b"\xff\n", "not valid UTF-8"),
    ],
)
def test_json_loading_fails_closed(
    tmp_path: Path,
    content: bytes,
    expected: str,
) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(content)
    with pytest.raises(checker.ContractInputError, match=expected):
        checker.load_json_object(path)


def test_missing_symlink_and_oversized_inputs_fail_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(checker.ContractInputError, match="missing or unreadable"):
        checker.load_json_object(missing)

    target = _write_json(tmp_path / "target.json", {})
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(checker.ContractInputError, match="must not be a symlink"):
        checker.load_json_object(link)

    oversized = tmp_path / "large.json"
    oversized.write_bytes(b"{" + b" " * checker.MAX_JSON_BYTES + b"}")
    with pytest.raises(checker.ContractInputError, match="byte limit"):
        checker.load_json_object(oversized)


def test_main_validates_canonical_and_compatible_update(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert checker.main(["--root", str(ROOT)]) == 0
    canonical_output = capsys.readouterr()
    assert "VALID_EXAMPLES profiles=2 examples=3" in canonical_output.out
    assert canonical_output.err == ""

    baseline, candidate = _candidate_update()
    baseline_path = _write_json(tmp_path / "baseline.json", baseline)
    candidate_path = _write_json(tmp_path / "candidate.json", candidate)
    assert checker.main(
        [
            "--root",
            str(ROOT),
            "--baseline-profile",
            str(baseline_path),
            "--candidate-profile",
            str(candidate_path),
        ]
    ) == 0
    update_output = capsys.readouterr()
    assert "COMPATIBLE_PINNED_UPDATE" in update_output.out
    assert update_output.err == ""


def test_main_rejects_partial_args_and_incompatible_update(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline, candidate = _candidate_update()
    baseline_path = _write_json(tmp_path / "baseline.json", baseline)
    candidate_path = _write_json(tmp_path / "candidate.json", candidate)

    assert checker.main(
        ["--root", str(ROOT), "--baseline-profile", str(baseline_path)]
    ) == 2
    assert "must be provided together" in capsys.readouterr().err

    candidate["profile_state"] = "shadow"
    _write_json(candidate_path, candidate)
    assert checker.main(
        [
            "--root",
            str(ROOT),
            "--baseline-profile",
            str(baseline_path),
            "--candidate-profile",
            str(candidate_path),
        ]
    ) == 1
    output = capsys.readouterr()
    assert "INCOMPATIBLE" in output.err
    assert "candidate profile must remain disabled" in output.out


def test_main_reports_unreadable_contract_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert checker.main(["--root", str(tmp_path)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_main_reports_invalid_candidate_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = _write_json(tmp_path / "baseline.json", _production_profile())
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{invalid}\n", encoding="utf-8")
    assert checker.main(
        [
            "--root",
            str(ROOT),
            "--baseline-profile",
            str(baseline),
            "--candidate-profile",
            str(candidate),
        ]
    ) == 2
    assert "invalid JSON" in capsys.readouterr().err
