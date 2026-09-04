"""Fail-closed contracts for project-private self-improvement policy."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from general_ludd.self_improve.private_policy import (
    MAX_POLICY_BYTES,
    MAX_POLICY_RULES,
    SELF_IMPROVE_POLICY_PATH,
    PolicyAccess,
    SelfImprovePolicyError,
    load_self_improve_policy,
    parse_self_improve_policy,
)


def _policy_json(
    *,
    default_access: str = "public",
    private_paths: list[object] | None = None,
    public_paths: list[object] | None = None,
) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "default_access": default_access,
            "private_paths": private_paths or [],
            "public_paths": public_paths or [],
        }
    )


def test_whole_path_gitignore_matching_is_rooted_unanchored_and_case_sensitive() -> None:
    policy = parse_self_improve_policy(
        _policy_json(
            private_paths=[
                "src/domain/pricing.py",
                "secrets/**",
                "private.txt",
            ]
        )
    )

    assert policy.is_private("src/domain/pricing.py")
    assert not policy.is_private("src/domain/pricing.py.backup")
    assert not policy.is_private("nested/src/domain/pricing.py")
    assert policy.is_private("secrets/nested/key.py")
    assert not policy.is_private("nested/secrets/key.py")
    assert policy.is_private("nested/private.txt")
    assert not policy.is_private("nested/PRIVATE.txt")


def test_private_wins_over_public_and_default_private_is_allowlist() -> None:
    policy = parse_self_improve_policy(
        _policy_json(
            default_access="private",
            private_paths=["src/open/overlap.py"],
            public_paths=["src/open/**", ".gludd/**"],
        )
    )

    assert not policy.is_private("src/open/ordinary.py")
    assert policy.access_for("src/open/overlap.py") is PolicyAccess.PRIVATE
    assert policy.is_private("src/closed/ordinary.py")
    assert policy.is_private(SELF_IMPROVE_POLICY_PATH)


def test_canonical_digest_is_independent_of_json_and_rule_order() -> None:
    first = parse_self_improve_policy(
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":["z/**","a.py"],"public_paths":["docs/**"]}'
    )
    second = parse_self_improve_policy(
        '{"public_paths":["docs/**"],"private_paths":["a.py","z/**"],'
        '"default_access":"public","schema_version":1}'
    )
    canonical = (
        '{"default_access":"public","private_paths":["a.py","z/**"],'
        '"public_paths":["docs/**"],"schema_version":1}'
    )

    assert first == second
    assert first.canonical_json == canonical
    assert first.digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_policy_file_is_private_even_when_explicitly_public() -> None:
    policy = parse_self_improve_policy(
        _policy_json(public_paths=[SELF_IMPROVE_POLICY_PATH])
    )

    assert policy.is_private(SELF_IMPROVE_POLICY_PATH)
    assert policy.is_private(f"{SELF_IMPROVE_POLICY_PATH}/child")


@pytest.mark.parametrize(
    "pattern",
    [
        "",
        " ",
        "/etc/passwd",
        "C:/Windows/System32",
        "../secret.py",
        "src/../secret.py",
        "src\\secret.py",
        "!src/public.py",
        "#not-a-comment.py",
        "./src/private.py",
        "src//private.py",
        "src/private.py ",
        "src/\x00private.py",
    ],
)
def test_ambiguous_or_escaping_patterns_are_rejected(pattern: str) -> None:
    with pytest.raises(SelfImprovePolicyError, match="invalid path rule"):
        parse_self_improve_policy(_policy_json(private_paths=[pattern]))


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/src/private.py",
        "C:/src/private.py",
        "../private.py",
        "src/../private.py",
        "src\\private.py",
        "./src/private.py",
        "src//private.py",
        "src/private.py/",
        "src/\x00private.py",
    ],
)
def test_ambiguous_or_escaping_candidate_paths_are_rejected(path: str) -> None:
    policy = parse_self_improve_policy(_policy_json())

    with pytest.raises(SelfImprovePolicyError, match="invalid repository path"):
        policy.is_private(path)


def test_duplicate_rules_and_duplicate_json_keys_are_rejected() -> None:
    with pytest.raises(SelfImprovePolicyError, match="duplicate private_paths rule"):
        parse_self_improve_policy(
            _policy_json(private_paths=["src/private.py", "src/private.py"])
        )
    with pytest.raises(SelfImprovePolicyError, match="duplicate object key"):
        parse_self_improve_policy(
            '{"schema_version":1,"schema_version":1,"default_access":"public",'
            '"private_paths":[],"public_paths":[]}'
        )


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "{}",
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":[],"public_paths":[],"extra":true}',
        '{"schema_version":true,"default_access":"public",'
        '"private_paths":[],"public_paths":[]}',
        '{"schema_version":2,"default_access":"public",'
        '"private_paths":[],"public_paths":[]}',
        '{"schema_version":1,"default_access":"shared",'
        '"private_paths":[],"public_paths":[]}',
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":"src/private.py","public_paths":[]}',
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":[3],"public_paths":[]}',
        "{malformed",
    ],
)
def test_schema_is_exact_and_strict(raw: str) -> None:
    with pytest.raises(SelfImprovePolicyError):
        parse_self_improve_policy(raw)


def test_policy_input_is_bounded_and_utf8() -> None:
    with pytest.raises(SelfImprovePolicyError, match="maximum size"):
        parse_self_improve_policy(b" " * (MAX_POLICY_BYTES + 1))
    with pytest.raises(SelfImprovePolicyError, match="UTF-8"):
        parse_self_improve_policy(b"\xff")
    with pytest.raises(SelfImprovePolicyError, match="maximum rule count"):
        parse_self_improve_policy(
            _policy_json(
                private_paths=[f"private/{index}.py" for index in range(MAX_POLICY_RULES + 1)]
            )
        )


def test_absent_policy_defaults_public_but_invalid_policy_fails_closed(
    tmp_path: Path,
) -> None:
    absent = load_self_improve_policy(tmp_path)

    assert absent.default_access is PolicyAccess.PUBLIC
    assert not absent.is_private("src/public.py")
    assert absent.is_private(SELF_IMPROVE_POLICY_PATH)

    policy_path = tmp_path / SELF_IMPROVE_POLICY_PATH
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text("{malformed", encoding="utf-8")
    with pytest.raises(SelfImprovePolicyError):
        load_self_improve_policy(tmp_path)


def test_policy_loader_rejects_symlink_instead_of_following_it(tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    target.write_text(_policy_json(), encoding="utf-8")
    policy_path = tmp_path / SELF_IMPROVE_POLICY_PATH
    policy_path.parent.mkdir(parents=True)
    policy_path.symlink_to(target)

    with pytest.raises(SelfImprovePolicyError, match="regular non-symlink file"):
        load_self_improve_policy(tmp_path)


def test_policy_loader_rejects_symlinked_policy_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "self-improve-policy.json").write_text(_policy_json(), encoding="utf-8")
    (tmp_path / ".gludd").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SelfImprovePolicyError, match="policy directory"):
        load_self_improve_policy(tmp_path)


def test_unencodable_surrogate_paths_fail_with_policy_error() -> None:
    surrogate = chr(0xD800)
    policy = parse_self_improve_policy(_policy_json())

    with pytest.raises(SelfImprovePolicyError, match="invalid path rule"):
        parse_self_improve_policy(_policy_json(private_paths=[surrogate]))
    with pytest.raises(SelfImprovePolicyError, match="invalid repository path"):
        policy.is_private(surrogate)


def test_pathspec_is_a_direct_bounded_runtime_dependency() -> None:
    pyproject_path = Path(__file__).parents[2] / "pyproject.toml"
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]

    assert "pathspec>=1.0.4,<2" in project["dependencies"]
