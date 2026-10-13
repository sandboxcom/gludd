"""Pin maintained module owners and exercise the real infrastructure call names."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit.test_native_role_module_arguments import BOOTSTRAP, ROOT

COLLECTION = ROOT / "collections/ansible_collections/general_ludd"
MODULES = (
    ("web_server/roles/ssl_config/tasks/main.yml", "Generate private key", "community.crypto.openssl_privatekey"),
    ("web_server/roles/ssl_config/tasks/main.yml", "Generate CSR", "community.crypto.openssl_csr"),
    ("web_server/roles/ssl_config/tasks/main.yml", "Generate self-signed certificate",
     "community.crypto.x509_certificate"),
    ("web_server/roles/forward_proxy/tasks/main.yml", "Generate basic auth file", "community.general.htpasswd"),
)


def _tasks(items: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for task in items:
        yield task
        for section in ("block", "rescue", "always"):
            yield from _tasks(task.get(section, []))


@pytest.mark.parametrize(("relative", "name", "fqcn"), MODULES)
def test_web_role_uses_the_maintained_collection_owner(relative: str, name: str, fqcn: str) -> None:
    items = yaml.safe_load((COLLECTION / relative).read_text(encoding="utf-8"))
    calls = [task for task in _tasks(items) if task.get("name") == name and fqcn in task]
    assert len(calls) == 1, f"{name} must use {fqcn}"
    assert "path" in calls[0][fqcn]
    assert not any(key.startswith("ansible.builtin.openssl_") or key == "ansible.builtin.htpasswd"
                   for task in _tasks(items) for key in task)


@pytest.mark.parametrize("invalid_option", (False, True))
def test_infra_scenario_uses_resolvable_native_modules_and_rejects_bad_options(
    invalid_option: bool, tmp_path: Path,
) -> None:
    relative = Path("agent/plugins/modules/molecule/default/converge.yml")
    target = tmp_path / "collections/ansible_collections/general_ludd" / relative
    target.parent.mkdir(parents=True)
    shutil.copy2(COLLECTION / relative, target)
    for dependency in (
        "agent/plugins/modules/infra_deploy.py",
        "agent/plugins/modules/infra_destroy.py",
        "agent/plugins/module_utils/gludd.py",
    ):
        copied = tmp_path / "collections/ansible_collections/general_ludd" / dependency
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(COLLECTION / dependency, copied)
    if invalid_option:
        playbook = yaml.safe_load(target.read_text(encoding="utf-8"))
        call = next(task for task in playbook[0]["tasks"]
                    if "general_ludd.agent.infra_deploy" in task)
        call["general_ludd.agent.infra_deploy"]["never_supported"] = True
        target.write_text(yaml.safe_dump(playbook), encoding="utf-8")
    environment = dict(os.environ)
    environment["ANSIBLE_COLLECTIONS_PATH"] = str(ROOT / "collections")
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", BOOTSTRAP,
         str(ROOT / "scripts/check_molecule_yaml.py"), str(tmp_path)],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        check=False, timeout=140,
    )
    output = result.stdout + result.stderr
    if invalid_option:
        assert result.returncode != 0, output
        assert "never_supported" in output and "Unsupported parameters" in output, output
    else:
        assert result.returncode == 0, output
        assert "MOLECULE_VALIDATION_LINT_END rc=0" in output
        assert "Unable to load module" not in output
