"""Managed-host Python boundary contracts for the forensics collection."""

from __future__ import annotations

import importlib
import re
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from ansible.module_utils import basic as ansible_basic

COLLECTION_ROOT = Path(__file__).resolve().parents[2]
ROLES_ROOT = COLLECTION_ROOT / "roles"
MODULE_FQCN = "general_ludd.forensics.forensic_analysis"
ANALYSIS_ROLES = {
    "dna_analyst": "dna",
    "fingerprint_analyst": "fingerprint",
    "forensics_coordinator": "chain_of_custody",
    "photo_forensics_analyst": "photo",
    "trace_evidence_examiner": "trace",
}
AMBIENT_PYTHON = re.compile(
    r"(?:^|[\s:'\"=])(?:/usr/bin/python3?|/usr/local/bin/python3?|python3?|py)(?:\s|$)"
)


def _tasks(role: str) -> list[dict[str, Any]]:
    value = yaml.safe_load((ROLES_ROOT / role / "tasks/main.yml").read_text(encoding="utf-8"))
    assert isinstance(value, list)
    return value


def _serialized_tasks(role: str) -> str:
    return str(yaml.safe_dump(_tasks(role), sort_keys=False))


def _load_adapter() -> Any:
    collections_root = str(COLLECTION_ROOT.parents[2])
    sys.path.insert(0, collections_root)
    try:
        return importlib.import_module(
            "ansible_collections.general_ludd.forensics.plugins.module_utils.forensic_adapter"
        )
    finally:
        sys.path.remove(collections_root)


def _load_module() -> Any:
    collections_root = str(COLLECTION_ROOT.parents[2])
    sys.path.insert(0, collections_root)
    try:
        return importlib.import_module(
            "ansible_collections.general_ludd.forensics.plugins.modules.forensic_analysis"
        )
    finally:
        sys.path.remove(collections_root)


def test_roles_use_packaged_fqcn_module_without_ambient_python() -> None:
    for role, operation in ANALYSIS_ROLES.items():
        serialized = _serialized_tasks(role)
        assert MODULE_FQCN in serialized
        assert f"operation: {operation}" in serialized
        assert "ansible.builtin.script" not in serialized
        assert AMBIENT_PYTHON.search(serialized) is None


def test_fqcn_module_is_packaged_at_ansible_resolution_path() -> None:
    module_path = COLLECTION_ROOT / "plugins/modules/forensic_analysis.py"
    source = module_path.read_text(encoding="utf-8")

    assert "AnsibleModule" in source
    assert "forensic_adapter" in source
    assert '"no_log": True' in source


def test_adapter_dispatches_supported_operations(tmp_path: Path) -> None:
    adapter = _load_adapter()

    dna = adapter.run_analysis(
        "dna",
        {
            "sample": {"id": "sample", "loci": {"D3S1358": [15, 17]}},
            "reference": {"id": "reference", "loci": {"D3S1358": [15, 17]}},
            "analysis_type": "str",
        },
    )
    fingerprint = adapter.run_analysis(
        "fingerprint",
        {"data": {"ridge_flow_description": "ulnar loop", "core_present": True, "delta_count": 1}},
    )
    chain = adapter.run_analysis("chain_of_custody", {"case_id": "CASE-1"})
    image_path = tmp_path / "evidence.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xd9")
    photo = adapter.run_analysis(
        "photo",
        {"image_path": str(image_path), "analysis_types": ["metadata"]},
    )
    trace = adapter.run_analysis(
        "trace",
        {
            "evidence_type": "fiber",
            "sample": {"color": "red", "material": "wool"},
            "reference": {"color": "red", "material": "wool"},
            "output_dir": str(tmp_path),
        },
    )

    assert dna["sample_id"] == "sample"
    assert fingerprint["pattern_type"] == "LOOP"
    assert chain["case_id"] == "CASE-1"
    assert chain["status"] == "initialized"
    assert photo["analysis_types_run"] == ["metadata"]
    assert trace["output_path"].endswith("trace_evidence_verdict.json")


def test_adapter_fails_closed_for_unknown_or_incomplete_operations() -> None:
    adapter = _load_adapter()

    with pytest.raises(ValueError, match="unsupported forensic operation"):
        adapter.run_analysis("unknown", {})
    with pytest.raises(ValueError, match="case_id"):
        adapter.run_analysis("chain_of_custody", {})
    with pytest.raises(ValueError, match="sample must be an object"):
        adapter.run_analysis(
            "dna",
            {"sample": [], "reference": {}, "analysis_type": "str"},
        )
    with pytest.raises(ValueError, match="unsupported photo analysis"):
        adapter.run_analysis(
            "photo",
            {"image_path": "/unused", "analysis_types": ["unknown"]},
        )


def test_module_main_returns_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    output: dict[str, Any] = {}

    class ExitModule(RuntimeError):
        """Signal a fake Ansible module exit."""

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = {"operation": "chain_of_custody", "case_id": "CASE-2"}

        def exit_json(self, **kwargs: Any) -> None:
            output.update(kwargs)
            raise ExitModule

        def fail_json(self, **_kwargs: Any) -> None:
            raise AssertionError("unexpected failure")

    monkeypatch.setattr(module, "AnsibleModule", FakeAnsibleModule)
    with pytest.raises(ExitModule):
        module.main()

    assert output["changed"] is False
    assert output["result"]["case_id"] == "CASE-2"


def test_module_main_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()

    class FailModule(RuntimeError):
        """Signal a fake Ansible module failure."""

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = {"operation": "unknown"}

        def exit_json(self, **_kwargs: Any) -> None:
            raise AssertionError("unexpected success")

        def fail_json(self, **kwargs: Any) -> None:
            assert "unsupported forensic operation" in kwargs["msg"]
            raise FailModule

    monkeypatch.setattr(module, "AnsibleModule", FakeAnsibleModule)
    with pytest.raises(FailModule):
        module.main()


def test_fqcn_module_script_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    module_path = COLLECTION_ROOT / "plugins/modules/forensic_analysis.py"

    class ExitModule(RuntimeError):
        """Signal a fake Ansible module exit."""

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = {"operation": "chain_of_custody", "case_id": "CASE-3"}

        def exit_json(self, **kwargs: Any) -> None:
            assert kwargs["result"]["case_id"] == "CASE-3"
            raise ExitModule

        def fail_json(self, **_kwargs: Any) -> None:
            raise AssertionError("unexpected failure")

    monkeypatch.setattr(ansible_basic, "AnsibleModule", FakeAnsibleModule)
    with pytest.raises(ExitModule):
        runpy.run_path(str(module_path), run_name="__main__")
