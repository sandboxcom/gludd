"""Unit tests for the openbao_break_glass_backup role + gludd_break_glass module.

TDD: this file was written FIRST and red against an empty tree, then the role,
module, defaults, handlers, meta, README, and playbook were created to make it
green. No assertion here is satisfied by a missing file — each test pinpoints a
load-bearing artifact (file path, default value, no_log flag, command shape).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COLLECTION_ROOT = REPO_ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
ROLE_DIR = COLLECTION_ROOT / "roles" / "openbao_break_glass_backup"
MODULE_PATH = COLLECTION_ROOT / "plugins" / "modules" / "gludd_break_glass.py"


class TestRoleHasRequiredTaskFiles:
    def test_tasks_main_exists(self) -> None:
        assert (ROLE_DIR / "tasks" / "main.yml").is_file(), "tasks/main.yml missing"

    def test_defaults_main_exists(self) -> None:
        assert (ROLE_DIR / "defaults" / "main.yml").is_file(), "defaults/main.yml missing"

    def test_handlers_main_exists(self) -> None:
        assert (ROLE_DIR / "handlers" / "main.yml").is_file(), "handlers/main.yml missing"

    def test_meta_main_exists(self) -> None:
        assert (ROLE_DIR / "meta" / "main.yml").is_file(), "meta/main.yml missing"

    def test_readme_exists(self) -> None:
        assert (ROLE_DIR / "README.md").is_file(), "README.md missing"

    def test_tasks_main_is_valid_yaml(self) -> None:
        text = (ROLE_DIR / "tasks" / "main.yml").read_text()
        parsed = yaml.safe_load(text)
        assert isinstance(parsed, list), "tasks/main.yml must be a YAML list"

    def test_defaults_main_is_valid_yaml(self) -> None:
        text = (ROLE_DIR / "defaults" / "main.yml").read_text()
        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict), "defaults/main.yml must be a YAML mapping"


class TestDefaultsSensible:
    @classmethod
    def setup_class(cls) -> None:
        cls.defaults = yaml.safe_load(
            (ROLE_DIR / "defaults" / "main.yml").read_text()
        )

    def test_backup_dir_default(self) -> None:
        assert self.defaults.get("backup_dir") == "/var/backups/gludd/openbao"

    def test_backup_retention_days_is_int(self) -> None:
        v = self.defaults.get("backup_retention_days")
        assert isinstance(v, int), "backup_retention_days must be int"
        assert v >= 1, "backup_retention_days must be >= 1"

    def test_backup_filename_template_exists(self) -> None:
        assert "backup_filename" in self.defaults

    def test_gpg_recipient_default(self) -> None:
        assert "gpg_recipient" in self.defaults

    def test_openbao_addr_is_https_or_loopback(self) -> None:
        addr = str(self.defaults.get("openbao_addr", ""))
        assert addr.startswith("https://") or addr.startswith("http://127.0.0.1"), (
            f"openbao_addr {addr!r} must be https:// or http://127.0.0.1 — "
            "a plaintext non-loopback URL would leak the OpenBao token"
        )

    def test_openbao_token_source_choices(self) -> None:
        v = str(self.defaults.get("openbao_token_source", ""))
        assert v in {"env", "secret"}, (
            "openbao_token_source must be 'env' (VAULT_TOKEN) or 'secret' (gludd SecretsManager)"
        )


class TestGpgKeyGenerationCommandWellFormed:
    @classmethod
    def setup_class(cls) -> None:
        cls.tasks_text = (ROLE_DIR / "tasks" / "main.yml").read_text()

    def test_uses_no_protection(self) -> None:
        assert "%no-protection" in self.tasks_text, (
            "GPG key generation must use %no-protection for unattended operation"
        )

    def test_uses_batch_generate_key(self) -> None:
        assert "gpg --batch --generate-key" in self.tasks_text, (
            "must call gpg --batch --generate-key"
        )

    def test_generation_gated_on_when_clause(self) -> None:
        parsed = yaml.safe_load(self.tasks_text)
        gen = [
            t for t in parsed
            if isinstance(t, dict)
            and "Generate GPG key" in str(t.get("name", ""))
        ]
        assert gen, "missing task: 'Generate GPG key for user'"
        assert "when" in gen[0], "Generate task must be gated by a when: clause (idempotency)"

    def test_keycheck_uses_list_secret_keys(self) -> None:
        assert "gpg --list-secret-keys" in self.tasks_text, (
            "must probe gpg --list-secret-keys before generating"
        )

    def test_generation_marked_no_log(self) -> None:
        parsed = yaml.safe_load(self.tasks_text)
        gen = [
            t for t in parsed
            if isinstance(t, dict)
            and "Generate GPG key" in str(t.get("name", ""))
        ]
        assert gen and gen[0].get("no_log") is True, (
            "GPG key generation must set no_log: true (fingerprint/key material in output)"
        )


class TestGluddBreakGlassModuleExists:
    def test_module_file_exists(self) -> None:
        assert MODULE_PATH.is_file(), f"module missing: {MODULE_PATH}"


class TestModuleArgumentSpecComplete:
    @classmethod
    def setup_class(cls) -> None:
        assert MODULE_PATH.is_file(), f"module missing: {MODULE_PATH}"
        cls.src = MODULE_PATH.read_text()

    def test_argument_spec_keys(self) -> None:
        """The module's argument_spec must contain every required arg.

        Handles BOTH call shapes AnsibleModule accepts: ``argument_spec=`` as a
        keyword AND as the first positional arg. Handles BOTH literal-dict
        (``{"k": ...}``) and ``dict(k=...)`` invocations.
        """
        tree = ast.parse(self.src)
        spec: dict[str, dict] = {}

        def _extract_spec(value: ast.AST) -> None:
            # Two shapes: dict(...) call or {...} literal
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict":
                pairs = _kwargs_from_call(value)
            elif isinstance(value, ast.Dict):
                pairs = _pairs_from_dict_literal(value)
            else:
                return
            for k, v in pairs:
                if isinstance(k, str) and v is not None:
                    spec[k] = {}

        def _kwargs_from_call(call: ast.Call) -> list[tuple[Any, Any]]:
            return [
                (kw.arg, kw.value) for kw in call.keywords if kw.arg is not None
            ]

        def _pairs_from_dict_literal(d: ast.Dict) -> list[tuple[Any, Any]]:
            out = []
            for k, v in zip(d.keys, d.values, strict=False):
                if isinstance(k, ast.Constant):
                    out.append((k.value, v))
            return out

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # AnsibleModule(argument_spec=...) keyword form.
                for kw in node.keywords:
                    if kw.arg == "argument_spec":
                        _extract_spec(kw.value)
                # AnsibleModule({...}) positional form.
                if node.args:
                    _extract_spec(node.args[0])

        required = {"openbao_addr", "token", "output_path", "mode", "restore_source"}
        assert required.issubset(spec.keys()), (
            f"argument_spec missing keys: {required - set(spec.keys())}"
        )

    def test_mode_choices_snapshot_and_restore(self) -> None:
        src = self.src
        # Substring assertion is robust against either dict(...) or literal forms.
        assert '"snapshot"' in src and '"restore"' in src, (
            "module must declare mode choices 'snapshot' and 'restore'"
        )
        assert "choices" in src, "mode must have a choices= list"

    def test_module_has_documentation_block(self) -> None:
        assert "DOCUMENTATION" in self.src, "module must have a DOCUMENTATION block"

    def test_module_has_examples_block(self) -> None:
        assert "EXAMPLES" in self.src

    def test_module_has_return_block(self) -> None:
        assert "RETURN" in self.src


class TestPskNoLogSet:
    def test_token_arg_is_no_log(self) -> None:
        src = MODULE_PATH.read_text()
        # The token argument is declared as token=dict(type="str", required=True,
        # no_log=True). The presence of "token=dict(...)" with "no_log=True" in
        # the same call is the load-bearing requirement.
        assert "no_log=True" in src, (
            "module must declare no_log=True on the token argument"
        )
        # Confirm 'token' is the field carrying no_log.
        # Find the line containing 'no_log=True' and check 'token' appears
        # nearby (within the same arg block — search a window around the match).
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "no_log=True" in line:
                window = "\n".join(lines[max(0, i - 3):i + 1])
                if "token" in window:
                    return
        raise AssertionError("no_log=True is present but not attached to the token arg")


class TestRestoreModeCommandShape:
    def test_restore_endpoint_referenced(self) -> None:
        src = MODULE_PATH.read_text()
        assert "/v1/sys/storage/raft/restore" in src, (
            "restore mode must POST to /v1/sys/storage/raft/restore"
        )

    def test_snapshot_endpoint_referenced(self) -> None:
        src = MODULE_PATH.read_text()
        assert "/v1/sys/storage/raft/snapshot" in src, (
            "snapshot mode must GET /v1/sys/storage/raft/snapshot"
        )


class TestExamplePlaybook:
    def test_playbook_exists(self) -> None:
        assert (REPO_ROOT / "playbooks" / "openbao_backup.yml").is_file()

    def test_playbook_is_valid_yaml(self) -> None:
        text = (REPO_ROOT / "playbooks" / "openbao_backup.yml").read_text()
        parsed = yaml.safe_load(text)
        assert isinstance(parsed, list)
        assert len(parsed) >= 1
        joined = yaml.safe_dump(parsed)
        assert "openbao_break_glass_backup" in joined


class TestMoleculeScenarioExists:
    def test_scenario_dir_exists(self) -> None:
        p = REPO_ROOT / "molecule" / "playbooks" / "openbao_break_glass_backup"
        assert p.is_dir(), f"molecule scenario missing: {p}"
        assert (p / "molecule.yml").is_file()
