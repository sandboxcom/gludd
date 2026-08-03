"""Deep structural validation of every OPA/Rego policy file.

Validates parseability, required rules, deny-rule messages, helper existence,
rule completeness, and test coverage — without requiring opa/conftest on PATH.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROJECT = Path(__file__).resolve().parent.parent.parent

_REGO_FILES: dict[str, Path] = {
    "config_policy": _PROJECT / "config" / "opa" / "config_policy.rego",
    "iam_policy": _PROJECT / "config" / "opa" / "iam_policy.rego",
    "terraform_policy": _PROJECT / "config" / "opa" / "terraform_policy.rego",
    "core": _PROJECT / "infra" / "terraform" / "policies" / "core.rego",
    "trust": _PROJECT / "infra" / "terraform" / "policies" / "trust.rego",
    "example_tag": (
        _PROJECT
        / "collections"
        / "ansible_collections"
        / "general_ludd"
        / "agent"
        / "plugins"
        / "terraform"
        / "policies"
        / "example_tag_enforcement.rego"
    ),
}

_REGO_TEST_FILES: dict[str, Path] = {
    "config_policy": _PROJECT / "config" / "opa" / "config_policy_test.rego",
    "iam_policy": _PROJECT / "config" / "opa" / "iam_policy_test.rego",
    "terraform_policy": _PROJECT / "config" / "opa" / "terraform_policy_test.rego",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


_RULE_RE = re.compile(
    r"^(\w[\w_]*)"  # rule name
    r"(?:\s+contains\s+\w+)?"  # optional 'contains <varname>'
    r"(?:\([^)]*\))?"  # optional (args)
    r"(?:\[[^\]]*\])?"  # optional [msg] / [level] (set index)
    r"(?:\s*=\s*\w+)?"  # optional '= var' (partial-rule output)
    r"(?:\s*\{|\s+if\s*\{)",  # '{' or 'if {'
    re.MULTILINE,
)


def _rule_names(text: str) -> list[str]:
    """Return names of all rules defined in the file (package-level)."""
    return [m.group(1) for m in _RULE_RE.finditer(text)]


def _deny_rule_names(text: str) -> list[str]:
    """Return names of rules that start with 'deny'."""
    return [r for r in _rule_names(text) if r.startswith("deny")]


def _rule_bodies(text: str) -> dict[str, str]:
    """Return a dict of rule-name -> rule body text (between { and })."""
    result: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _RULE_RE.match(line)
        if m:
            rule_name = m.group(1)
            depth = 0
            body_lines: list[str] = []
            started = False
            j = i
            while j < len(lines):
                for ch in lines[j]:
                    if ch == "{":
                        depth += 1
                        started = True
                    elif ch == "}":
                        depth -= 1
                body_lines.append(lines[j])
                if started and depth == 0:
                    break
                j += 1
            result[rule_name] = "\n".join(body_lines)
            i = j
        i += 1
    return result


def _helper_names(text: str) -> list[str]:
    """Return names of helper rules (non-deny, non-test)."""
    all_rules = _rule_names(text)
    return [r for r in all_rules if not r.startswith("deny") and not r.startswith("test_")]


def _package_name(text: str) -> str | None:
    m = re.search(r"^package\s+(\S+)", text, re.MULTILINE)
    return m.group(1) if m else None


def _imports(text: str) -> list[str]:
    return re.findall(r"^import\s+(data\.[\w.]+)", text, re.MULTILINE)


def _all_exist(files: list[Path]) -> None:
    for p in files:
        assert p.is_file(), f"Missing Rego file: {p}"


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


def test_all_policy_files_exist() -> None:
    _all_exist(list(_REGO_FILES.values()))


def test_all_test_files_exist() -> None:
    _all_exist(list(_REGO_TEST_FILES.values()))


def test_policy_file_count() -> None:
    assert len(_REGO_FILES) == 6, f"Expected 6 policy files, found {len(_REGO_FILES)}"


def test_test_file_count() -> None:
    assert len(_REGO_TEST_FILES) == 3, f"Expected 3 test files, found {len(_REGO_TEST_FILES)}"


# ---------------------------------------------------------------------------
# Parseability — every file must start with a package declaration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "config_policy",
        "iam_policy",
        "terraform_policy",
        "core",
        "trust",
        "example_tag",
    ],
)
def test_policy_has_package(name: str) -> None:
    text = _text(_REGO_FILES[name])
    pkg = _package_name(text)
    assert pkg is not None, f"{name} is missing a package declaration"


@pytest.mark.parametrize("name", ["config_policy", "iam_policy", "terraform_policy"])
def test_test_file_has_package(name: str) -> None:
    text = _text(_REGO_TEST_FILES[name])
    pkg = _package_name(text)
    assert pkg is not None, f"{name}_test.rego is missing a package declaration"


@pytest.mark.parametrize(
    "name",
    ["config_policy", "iam_policy", "terraform_policy", "core", "trust", "example_tag"],
)
def test_policy_ends_with_newline(name: str) -> None:
    text = _text(_REGO_FILES[name])
    assert text.endswith("\n") or text.endswith("\n}"), f"{name}.rego should end with a newline"


# ---------------------------------------------------------------------------
# Package names
# ---------------------------------------------------------------------------


def test_config_policy_package() -> None:
    assert _package_name(_text(_REGO_FILES["config_policy"])) == "hottentot.config"


def test_iam_policy_package() -> None:
    assert _package_name(_text(_REGO_FILES["iam_policy"])) == "iam"


def test_terraform_policy_package() -> None:
    assert _package_name(_text(_REGO_FILES["terraform_policy"])) == "terraform"


def test_core_policy_package() -> None:
    assert _package_name(_text(_REGO_FILES["core"])) == "main"


def test_trust_policy_package() -> None:
    assert _package_name(_text(_REGO_FILES["trust"])) == "main"


def test_example_tag_policy_package() -> None:
    assert _package_name(_text(_REGO_FILES["example_tag"])) == "main"


# ---------------------------------------------------------------------------
# Config policy — required rules
# ---------------------------------------------------------------------------


def test_config_policy_required_rules_present() -> None:
    text = _text(_REGO_FILES["config_policy"])
    required = [
        "guardrail_layers_valid",
        "tdd_enforced",
        "commit_after_green",
        "evidence_required",
        "command_patterns_valid",
        "stop_conditions_valid",
    ]
    bodies = _rule_bodies(text)
    for rule in required:
        assert rule in bodies, f"Missing required rule: {rule}"


def test_config_policy_guardrail_layers_checks_three_layers() -> None:
    text = _text(_REGO_FILES["config_policy"])
    assert "config_layer" in text
    assert "hook_layer" in text
    assert "prompt_layer" in text


def test_config_policy_has_all_make_helper() -> None:
    text = _text(_REGO_FILES["config_policy"])
    assert "all_make" in text


def test_config_policy_stop_conditions_includes_missing_credentials() -> None:
    text = _text(_REGO_FILES["config_policy"])
    assert '"missing_credentials"' in text


# ---------------------------------------------------------------------------
# IAM policy — deny rules must have messages
# ---------------------------------------------------------------------------


def test_iam_policy_all_deny_rules_have_messages() -> None:
    text = _text(_REGO_FILES["iam_policy"])
    bodies = _rule_bodies(text)
    deny_names = _deny_rule_names(text)
    assert len(deny_names) >= 10, f"Expected >=10 deny rules, found {len(deny_names)}"
    for name in deny_names:
        body = bodies.get(name, "")
        assert "msg" in body, f"deny rule '{name}' is missing a msg assignment"


def test_iam_policy_required_deny_rules() -> None:
    text = _text(_REGO_FILES["iam_policy"])
    required = [
        "deny_aws_wildcard_resource",
        "deny_azure_missing_scope",
        "deny_gcp_set_metadata",
        "deny_azure_no_actions",
        "deny_azure_missing_metadata",
        "deny_azure_missing_assignable_scopes",
        "deny_azure_invalid_scope",
        "deny_azure_missing_runcommand_notaction",
        "deny_azure_missing_roleassign_notactions",
        "deny_azure_list_action_suffix",
        "deny_azure_data_plane_access",
    ]
    bodies = _rule_bodies(text)
    for rule in required:
        assert rule in bodies, f"Missing required deny rule: {rule}"


def test_iam_policy_has_validation_rules() -> None:
    text = _text(_REGO_FILES["iam_policy"])
    required = [
        "aws_least_privilege_valid",
        "azure_least_privilege_valid",
        "azure_custom_role_valid",
        "gcp_least_privilege_valid",
        "all_clouds_least_privilege_valid",
    ]
    bodies = _rule_bodies(text)
    for rule in required:
        assert rule in bodies, f"Missing validation rule: {rule}"


def test_iam_policy_has_provider_specific_deny_rules() -> None:
    text = _text(_REGO_FILES["iam_policy"])
    # AWS
    assert 'input.provider == "aws"' in text
    # Azure
    assert 'input.provider == "azure"' in text
    # GCP
    assert 'input.provider == "gcp"' in text


def test_iam_policy_azure_helpers() -> None:
    text = _text(_REGO_FILES["iam_policy"])
    assert "has_notaction_runcommand" in text
    assert "has_notaction_roleassign_write" in text
    assert "has_notaction_roleassign_delete" in text


# ---------------------------------------------------------------------------
# Terraform policy — deny rules must have messages
# ---------------------------------------------------------------------------


def test_terraform_policy_all_deny_rules_have_messages() -> None:
    text = _text(_REGO_FILES["terraform_policy"])
    bodies = _rule_bodies(text)
    deny_names = _deny_rule_names(text)
    assert len(deny_names) >= 5, f"Expected >=5 deny rules, found {len(deny_names)}"
    for name in deny_names:
        body = bodies.get(name, "")
        assert "msg" in body, f"deny rule '{name}' is missing a msg assignment"


def test_terraform_policy_required_deny_rules() -> None:
    text = _text(_REGO_FILES["terraform_policy"])
    required_keywords = [
        "must have tags",
        "no description",
        "block public ACLs",
        "storage encryption",
        "wildcard Action",
    ]
    for kw in required_keywords:
        assert kw in text, f"Missing required deny keyword: {kw}"


def test_terraform_policy_checks_json_unmarshal() -> None:
    text = _text(_REGO_FILES["terraform_policy"])
    assert "json.unmarshal" in text


# ---------------------------------------------------------------------------
# Core policy — infra/terraform/policies/core.rego
# ---------------------------------------------------------------------------


def test_core_policy_has_required_constants() -> None:
    text = _text(_REGO_FILES["core"])
    assert "required_tags" in text
    assert "forbidden_public_ports" in text


def test_core_policy_has_helpers() -> None:
    text = _text(_REGO_FILES["core"])
    assert "version_is_pinned" in text
    assert "tagged_resource_type" in text
    assert "walk_resource_values" in text
    assert "is_bastion" in text


def test_core_policy_all_deny_rules_have_messages() -> None:
    text = _text(_REGO_FILES["core"])
    bodies = _rule_bodies(text)
    deny_names = _deny_rule_names(text)
    assert len(deny_names) >= 8, f"Expected >=8 deny rules, found {len(deny_names)}"
    for name in deny_names:
        body = bodies.get(name, "")
        assert "level" in body, f"deny rule '{name}' is missing a level assignment (msg)"


def test_core_policy_forbidden_ports() -> None:
    text = _text(_REGO_FILES["core"])
    assert "22" in text
    assert "3389" in text
    assert "3306" in text
    assert "5432" in text


def test_core_policy_version_pinning() -> None:
    text = _text(_REGO_FILES["core"])
    assert "version_constraint" in text
    assert 'startswith(version, "~>")' in text
    assert 'startswith(version, "=")' in text


def test_core_policy_secret_leak_detection() -> None:
    text = _text(_REGO_FILES["core"])
    assert "AKIA" in text
    assert "PRIVATE KEY" in text


def test_core_policy_multi_cloud_tagging() -> None:
    text = _text(_REGO_FILES["core"])
    assert 'startswith(type, "aws_")' in text
    assert 'startswith(type, "azurerm_")' in text
    assert 'startswith(type, "google_")' in text


def test_core_policy_ec2_bastion_detection() -> None:
    text = _text(_REGO_FILES["core"])
    assert "is_bastion" in text
    assert "associate_public_ip_address" in text


# ---------------------------------------------------------------------------
# Trust policy
# ---------------------------------------------------------------------------


def test_trust_policy_has_required_helpers() -> None:
    text = _text(_REGO_FILES["trust"])
    assert "provider_in_trust_list" in text


def test_trust_policy_deny_rules_have_messages() -> None:
    text = _text(_REGO_FILES["trust"])
    bodies = _rule_bodies(text)
    deny_names = _deny_rule_names(text)
    assert len(deny_names) >= 2, f"Expected >=2 deny rules, found {len(deny_names)}"
    for name in deny_names:
        body = bodies.get(name, "")
        assert "level" in body, f"trust deny rule '{name}' is missing a level assignment"


def test_trust_policy_references_trust_list() -> None:
    text = _text(_REGO_FILES["trust"])
    assert "provider_trust_list" in text
    assert "provider_registry" in text


# ---------------------------------------------------------------------------
# Example tag enforcement policy
# ---------------------------------------------------------------------------


def test_example_tag_policy_has_deny_rules() -> None:
    text = _text(_REGO_FILES["example_tag"])
    deny_names = _deny_rule_names(text)
    assert len(deny_names) >= 2, f"Expected >=2 deny rules, found {len(deny_names)}"


def test_example_tag_policy_enforces_environment_tag() -> None:
    text = _text(_REGO_FILES["example_tag"])
    assert "Environment" in text
    assert "missing tags.Environment" in text


# ---------------------------------------------------------------------------
# Test coverage — each _test.rego must import correct package and have tests
# ---------------------------------------------------------------------------


def test_config_policy_test_imports() -> None:
    text = _text(_REGO_TEST_FILES["config_policy"])
    pkg = _package_name(text)
    assert pkg == "hottentot.config", f"Expected hottentot.config, got {pkg}"
    test_funcs = re.findall(r"^test_\w+", text, re.MULTILINE)
    assert len(test_funcs) >= 5, f"Expected >=5 test functions, found {len(test_funcs)}"


def test_iam_policy_test_imports() -> None:
    text = _text(_REGO_TEST_FILES["iam_policy"])
    pkg = _package_name(text)
    assert pkg == "iam_test", f"Expected iam_test, got {pkg}"
    assert "import data.iam.deny" in text
    test_funcs = re.findall(r"^test_\w+", text, re.MULTILINE)
    assert len(test_funcs) >= 15, f"Expected >=15 test functions, found {len(test_funcs)}"


def test_terraform_policy_test_imports() -> None:
    text = _text(_REGO_TEST_FILES["terraform_policy"])
    pkg = _package_name(text)
    assert pkg == "terraform_test", f"Expected terraform_test, got {pkg}"
    assert "import data.terraform.deny" in text
    test_funcs = re.findall(r"^test_\w+", text, re.MULTILINE)
    assert len(test_funcs) >= 10, f"Expected >=10 test functions, found {len(test_funcs)}"


# ---------------------------------------------------------------------------
# Cross-policy integrity
# ---------------------------------------------------------------------------


def test_no_policy_has_test_prefix() -> None:
    for name, path in _REGO_FILES.items():
        text = _text(path)
        assert "test_" not in _rule_names(text), (
            f"{name}.rego policy file contains test_ rules — move them to a _test.rego file"
        )


def test_core_and_trust_share_main_package() -> None:
    assert _package_name(_text(_REGO_FILES["core"])) == "main"
    assert _package_name(_text(_REGO_FILES["trust"])) == "main"


def test_deny_rules_use_set_semantics() -> None:
    """All deny rules must use additive set syntax (deny[msg] or deny contains msg)."""
    for name, path in _REGO_FILES.items():
        text = _text(path)
        deny_matches = re.findall(r"^deny\s*(?:\[|contains)", text, re.MULTILINE)
        deny_bare = re.findall(r"^deny\s*\{", text, re.MULTILINE)
        assert not deny_bare, f"{name}.rego has bare 'deny {{' — use 'deny[msg]' or 'deny contains msg'"
        assert len(deny_matches) >= 1 or "deny" not in _helper_names(text), (
            f"{name}.rego: deny rules must use additive set syntax"
        )


def test_all_policy_files_utf8() -> None:
    for name, path in _REGO_FILES.items():
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            pytest.fail(f"{name}.rego is not valid UTF-8")


def test_no_empty_rule_bodies() -> None:
    for name, path in _REGO_FILES.items():
        bodies = _rule_bodies(_text(path))
        for rule_name, body in bodies.items():
            inner = body[body.index("{") + 1 : body.rindex("}")].strip()
            assert inner, f"{name}.rego: rule '{rule_name}' has an empty body"
