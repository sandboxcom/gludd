"""Tests for infra/terraform static modules (Phase 1 of TERRAFORM_INFRA_STRUCTURE.md).

Scope:
- Every module dir has main.tf, variables.tf, outputs.tf.
- Every module main.tf parses as valid HCL (python-hcl2 if installed, else a
  structural assertion that it contains a `resource` or `module` block).
- `terraform validate` is run if the terraform binary is present; tests skip
  cleanly otherwise.
- Modules contain no hardcoded provider credentials (no access_key / secret_key
  / password literals).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = REPO_ROOT / "infra" / "terraform" / "modules"

EXPECTED_MODULES = {"vllm-server", "llamacpp-server", "gpu-cost-watchdog", "network"}
REQUIRED_FILES = ("main.tf", "variables.tf", "outputs.tf")

# Credential attribute names that must never appear as literal HCL attributes.
# (Provider creds arrive via env / SecretsManager, never tfvars/HCL literals.)
_CREDENTIAL_PATTERNS = [
    re.compile(r'\baccess_key\s*=', re.IGNORECASE),
    re.compile(r'\bsecret_key\s*=', re.IGNORECASE),
    re.compile(r'\bsecret_access_key\s*=', re.IGNORECASE),
    re.compile(r'\bpassword\s*=', re.IGNORECASE),
    re.compile(r'\bapi_token\s*=', re.IGNORECASE),
    re.compile(r'\bprivate_key\s*=', re.IGNORECASE),
]


def _all_module_dirs() -> list[Path]:
    if not MODULES_DIR.is_dir():
        return []
    return sorted(p for p in MODULES_DIR.iterdir() if p.is_dir())


def _has_terraform_binary() -> bool:
    return shutil.which("terraform") is not None


def _try_import_hcl2():
    try:
        import hcl2  # type: ignore[import-not-found]
    except ImportError:
        return None
    return hcl2


def _strip_comments(text: str) -> str:
    """Drop leading-`#` comment lines so docstrings mentioning `access_key`
    (e.g. the AWS creds note in examples) don't trip the credential check.
    Real credential literals are uncommented HCL.
    """
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )


# ---------------------------------------------------------------------------
# Structural: every module has the required files.
# ---------------------------------------------------------------------------


class TestModuleStructure:
    def test_modules_dir_exists(self):
        assert MODULES_DIR.is_dir(), f"expected {MODULES_DIR} to exist"

    def test_expected_modules_present(self):
        actual = {p.name for p in _all_module_dirs()}
        missing = EXPECTED_MODULES - actual
        assert not missing, f"missing module dirs: {missing}"

    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_module_has_required_files(self, module_name: str):
        mod = MODULES_DIR / module_name
        assert mod.is_dir(), f"module dir missing: {mod}"
        for fname in REQUIRED_FILES:
            f = mod / fname
            assert f.is_file(), f"missing {fname} in module {module_name}"


# ---------------------------------------------------------------------------
# HCL validity: parse with hcl2 if available, else structural block check.
# ---------------------------------------------------------------------------


def _assert_structurally_valid_hcl(text: str, path: Path):
    """Fallback when python-hcl2 is not installed.

    Asserts the file has at least one top-level HCL block (`resource`, `module`,
    `variable`, `output`, `locals`, `data`, `provider`, or `terraform`).
    """
    block_pattern = re.compile(
        r'^(resource|module|variable|output|locals|data|provider|terraform)\b',
        re.MULTILINE,
    )
    assert block_pattern.search(text), (
        f"{path}: no top-level HCL block found (resource/module/variable/output/...)"
    )


class TestHclValidity:
    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_main_tf_parses(self, module_name: str):
        main_tf = MODULES_DIR / module_name / "main.tf"
        text = main_tf.read_text()
        hcl2 = _try_import_hcl2()
        if hcl2 is not None:
            import io

            ast = hcl2.load(io.StringIO(text))
            assert isinstance(ast, dict), f"{main_tf}: hcl2 did not return a dict"
            assert len(ast) > 0, f"{main_tf}: hcl2 parsed zero top-level blocks"
        else:
            _assert_structurally_valid_hcl(text, main_tf)

    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_variables_tf_parses(self, module_name: str):
        variables_tf = MODULES_DIR / module_name / "variables.tf"
        text = variables_tf.read_text()
        hcl2 = _try_import_hcl2()
        if hcl2 is not None:
            import io

            ast = hcl2.load(io.StringIO(text))
            assert isinstance(ast, dict), f"{variables_tf}: hcl2 did not return a dict"
        else:
            _assert_structurally_valid_hcl(text, variables_tf)

    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_outputs_tf_parses(self, module_name: str):
        outputs_tf = MODULES_DIR / module_name / "outputs.tf"
        text = outputs_tf.read_text()
        hcl2 = _try_import_hcl2()
        if hcl2 is not None:
            import io

            ast = hcl2.load(io.StringIO(text))
            assert isinstance(ast, dict), f"{outputs_tf}: hcl2 did not return a dict"
        else:
            _assert_structurally_valid_hcl(text, outputs_tf)


# ---------------------------------------------------------------------------
# terraform fmt -check + terraform validate (skipped if binary absent).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _has_terraform_binary(), reason="terraform binary not installed")
class TestTerraformBinaryValidate:
    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_module_fmt_and_validate(self, module_name: str, tmp_path: Path):
        mod_src = MODULES_DIR / module_name
        # Copy into tmp_path so `terraform init` is hermetic and cached.
        mod_dst = tmp_path / module_name
        mod_dst.mkdir()
        for fname in REQUIRED_FILES:
            (mod_dst / fname).write_text((mod_src / fname).read_text())

        # fmt -check
        fmt = subprocess.run(
            ["terraform", "fmt", "-check", "-diff", str(mod_dst)],
            capture_output=True,
            text=True,
        )
        assert fmt.returncode == 0, (
            f"terraform fmt -check failed for {module_name}:\n"
            f"stdout:\n{fmt.stdout}\nstderr:\n{fmt.stderr}"
        )

        # init (no provider downloads expected: modules use only terraform_data)
        init = subprocess.run(
            ["terraform", "init", "-backend=false", "-input=false"],
            cwd=str(mod_dst),
            capture_output=True,
            text=True,
        )
        assert init.returncode == 0, (
            f"terraform init failed for {module_name}:\n"
            f"stdout:\n{init.stdout}\nstderr:\n{init.stderr}"
        )

        # validate
        validate = subprocess.run(
            ["terraform", "validate"],
            cwd=str(mod_dst),
            capture_output=True,
            text=True,
        )
        assert validate.returncode == 0, (
            f"terraform validate failed for {module_name}:\n"
            f"stdout:\n{validate.stdout}\nstderr:\n{validate.stderr}"
        )


# ---------------------------------------------------------------------------
# Security: no hardcoded provider credentials.
# ---------------------------------------------------------------------------


class TestNoHardcodedCredentials:
    @pytest.mark.parametrize("module_name", sorted(EXPECTED_MODULES))
    def test_no_credential_literals(self, module_name: str):
        mod = MODULES_DIR / module_name
        for fname in REQUIRED_FILES:
            f = mod / fname
            if not f.is_file():
                continue
            active = _strip_comments(f.read_text())
            for pat in _CREDENTIAL_PATTERNS:
                assert not pat.search(active), (
                    f"{f}: credential literal matched by {pat.pattern!r}"
                )

    def test_examples_are_placeholders_only(self):
        examples_dir = REPO_ROOT / "infra" / "terraform" / "examples"
        assert examples_dir.is_dir(), f"expected {examples_dir} to exist"
        for ex in examples_dir.glob("*.tfvars.example"):
            active = _strip_comments(ex.read_text())
            for pat in _CREDENTIAL_PATTERNS:
                assert not pat.search(active), (
                    f"{ex}: credential literal matched by {pat.pattern!r}"
                )
