"""Native Frictionless admission contracts for the AI/ML dataset engineer."""

from __future__ import annotations

import hashlib
import json
import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from scripts.check_collection_python_boundary import scan_collections

ROOT = Path(__file__).resolve().parents[2]
COLLECTIONS = ROOT / "collections"
if str(COLLECTIONS) not in sys.path:
    sys.path.insert(0, str(COLLECTIONS))

from ansible_collections.general_ludd.ai_ml.plugins.action import (  # noqa: E402
    dataset_admit as action_plugin,
)
from ansible_collections.general_ludd.ai_ml.plugins.action.dataset_admit import (  # noqa: E402
    ActionModule,
    execute_action,
)
from ansible_collections.general_ludd.ai_ml.plugins.module_utils import (  # noqa: E402
    dataset_admission,
)
from ansible_collections.general_ludd.ai_ml.plugins.modules import (  # noqa: E402
    dataset_admit as module_stub,
)


def _write_dataset(root: Path, *, invalid: bool = False) -> tuple[Path, Path]:
    schema = root / "schema.json"
    resource = root / "records.csv"
    schema.write_text(
        json.dumps(
            {
                "fields": [
                    {"name": "id", "type": "integer", "constraints": {"required": True}},
                    {"name": "name", "type": "string", "constraints": {"required": True}},
                ],
                "primaryKey": "id",
            }
        ),
        encoding="utf-8",
    )
    bad_id = "not-an-integer" if invalid else "2"
    resource.write_text(f"id,name\n1,Ada\n{bad_id},Grace\n", encoding="utf-8")
    return schema, resource


def _args(root: Path) -> dict[str, Any]:
    return {
        "root": str(root),
        "resources": ["records.csv"],
        "schema": "schema.json",
        "name": "training-records",
        "description": "A deterministic validation fixture.",
        "license": "CC0-1.0",
    }


def _canonical_digest(card: dict[str, Any]) -> str:
    encoded = json.dumps(
        card,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_real_csv_schema_admission_is_stable_and_content_bound(tmp_path: Path) -> None:
    schema, resource = _write_dataset(tmp_path)
    before = {path.name: path.read_bytes() for path in (schema, resource)}

    first = dataset_admission.admit_dataset(**_args(tmp_path))
    second = dataset_admission.admit_dataset(**_args(tmp_path))

    assert first == second
    assert first["admitted"] is True
    assert first["changed"] is False
    assert first["resource_count"] == 1
    card = first["data_card"]
    assert card["validator"] == {"name": "frictionless", "version": "5.19.1"}
    assert card["schema"]["sha256"] == hashlib.sha256(schema.read_bytes()).hexdigest()
    assert card["resources"] == [
        {
            "bytes": resource.stat().st_size,
            "fields": 2,
            "path": "records.csv",
            "rows": 2,
            "sha256": hashlib.sha256(resource.read_bytes()).hexdigest(),
        }
    ]
    assert first["data_card_sha256"] == _canonical_digest(card)
    assert len(json.dumps(card).encode("utf-8")) <= 65_536
    assert str(tmp_path) not in json.dumps(first)
    assert before == {path.name: path.read_bytes() for path in (schema, resource)}


def test_invalid_rows_fail_closed_with_bounded_sanitized_errors(tmp_path: Path) -> None:
    _write_dataset(tmp_path, invalid=True)

    with pytest.raises(dataset_admission.DatasetAdmissionError) as raised:
        dataset_admission.admit_dataset(**_args(tmp_path))

    error = raised.value
    assert error.errors
    assert any(item["type"] == "type-error" for item in error.errors), error.errors
    assert all(set(item) <= {"field_number", "message", "row_number", "type"} for item in error.errors)
    assert all("\n" not in item["message"] for item in error.errors)
    assert str(tmp_path) not in json.dumps(error.errors)
    assert len(error.errors) <= 100


class _Report:
    def __init__(self, descriptor: dict[str, Any]) -> None:
        self._descriptor = descriptor

    def to_descriptor(self) -> dict[str, Any]:
        return self._descriptor


def _report_runner(descriptor: dict[str, Any]) -> Callable[..., _Report]:
    def run(*_args: Any, **_kwargs: Any) -> _Report:
        return _Report(descriptor)

    return run


@pytest.mark.parametrize(
    ("descriptor", "message", "truncated"),
    [
        (
            {"valid": True, "stats": {"tasks": 0, "errors": 0}, "warnings": [], "errors": [], "tasks": []},
            "exactly one task",
            False,
        ),
        (
            {
                "valid": False,
                "stats": {"tasks": 1, "errors": 101},
                "warnings": ["reached error limit"],
                "errors": [],
                "tasks": [
                    {
                        "type": "table",
                        "valid": False,
                        "labels": ["id", "name"],
                        "stats": {"errors": 101, "fields": 2, "rows": 2},
                        "warnings": ["reached error limit"],
                        "errors": [
                            {"type": "type-error", "message": f"bad row {index}", "rowNumber": index}
                            for index in range(1, 102)
                        ],
                    }
                ],
            },
            "validation failed",
            True,
        ),
        (
            {
                "valid": False,
                "stats": {"tasks": 1, "errors": 1},
                "warnings": [],
                "errors": [],
                "tasks": [
                    {
                        "type": "table",
                        "valid": False,
                        "labels": [],
                        "stats": {"errors": 1},
                        "warnings": [],
                        "errors": [
                            {
                                "type": "task-error",
                                "message": "validator task failed at /secret/controller/path\ntrace",
                            }
                        ],
                    }
                ],
            },
            "validation failed",
            False,
        ),
    ],
)
def test_empty_truncated_and_task_error_reports_fail_closed(
    tmp_path: Path,
    descriptor: dict[str, Any],
    message: str,
    truncated: bool,
) -> None:
    _write_dataset(tmp_path)

    with pytest.raises(dataset_admission.DatasetAdmissionError, match=message) as raised:
        dataset_admission.admit_dataset(
            **_args(tmp_path),
            validation_runner=_report_runner(descriptor),
        )

    assert raised.value.truncated is truncated
    assert len(raised.value.errors) <= 100
    assert "\n" not in json.dumps(raised.value.errors)
    assert "/secret/controller/path" not in json.dumps(raised.value.errors)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda root: {**_args(root), "resources": ["https://example.test/data.csv"]}, "local path"),
        (lambda root: {**_args(root), "resources": ["../outside.csv"]}, "escapes root"),
        (lambda root: {**_args(root), "resources": ["schema.json"]}, "CSV"),
        (lambda root: {**_args(root), "resources": []}, "at least one"),
        (lambda root: {**_args(root), "resources": ["records.csv"] * 33}, "at most 32"),
        (lambda root: {**_args(root), "uri": "https://service.test"}, "unsupported argument"),
    ],
)
def test_unsafe_or_ambiguous_inputs_fail_before_validation(
    tmp_path: Path,
    mutator: Callable[[Path], dict[str, Any]],
    message: str,
) -> None:
    _write_dataset(tmp_path)
    calls: list[object] = []

    def runner(*args: Any, **kwargs: Any) -> _Report:
        calls.append((args, kwargs))
        raise AssertionError("validation must not start")

    with pytest.raises((dataset_admission.DatasetAdmissionError, TypeError, ValueError), match=message):
        execute_action(mutator(tmp_path), validation_runner=runner)
    assert calls == []


def test_symlink_device_field_byte_and_card_caps_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _schema, resource = _write_dataset(tmp_path)
    link = tmp_path / "linked.csv"
    link.symlink_to(resource)
    with pytest.raises(dataset_admission.DatasetAdmissionError, match="symlink"):
        dataset_admission.admit_dataset(
            **{**_args(tmp_path), "resources": ["linked.csv"]}
        )

    with pytest.raises(dataset_admission.DatasetAdmissionError, match="regular file"):
        dataset_admission.admit_dataset(
            root="/",
            resources=["dev/null"],
            schema=str(tmp_path / "schema.json"),
        )

    oversized_schema = {
        "fields": [{"name": f"field_{index}", "type": "string"} for index in range(257)]
    }
    (tmp_path / "schema.json").write_text(json.dumps(oversized_schema), encoding="utf-8")
    with pytest.raises(dataset_admission.DatasetAdmissionError, match="at most 256"):
        dataset_admission.admit_dataset(**_args(tmp_path))

    _write_dataset(tmp_path)
    hashed: list[str] = []
    real_hash = dataset_admission._hash_file

    def track_hash(path: Path) -> str:
        hashed.append(path.name)
        return cast(str, real_hash(path))

    monkeypatch.setattr(dataset_admission, "_hash_file", track_hash)
    with pytest.raises(dataset_admission.DatasetAdmissionError, match="512 MiB"):
        dataset_admission.admit_dataset(
            **_args(tmp_path),
            _limits=dataset_admission.AdmissionLimits(
                max_total_bytes=_schema.stat().st_size + resource.stat().st_size - 1
            ),
        )
    assert hashed == ["schema.json"]

    with pytest.raises(dataset_admission.DatasetAdmissionError, match="64 KiB"):
        dataset_admission.admit_dataset(
            **{**_args(tmp_path), "description": "x" * 70_000},
        )


def test_input_schema_metadata_and_limit_guards_fail_closed(tmp_path: Path) -> None:
    _write_dataset(tmp_path)
    root_file = tmp_path / "not-a-root"
    root_file.write_text("plain file", encoding="utf-8")
    linked_root = tmp_path.parent / f"{tmp_path.name}-link"
    linked_root.symlink_to(tmp_path, target_is_directory=True)

    guarded_calls = [
        ({**_args(tmp_path), "root": ""}, "absolute local path"),
        ({**_args(tmp_path), "root": "https://example.test/data"}, "local path"),
        ({**_args(tmp_path), "root": "relative"}, "absolute local path"),
        ({**_args(tmp_path), "root": str(root_file)}, "directory"),
        ({**_args(tmp_path), "root": str(linked_root)}, "symlink"),
        ({**_args(tmp_path), "schema": "missing.json"}, "unavailable"),
    ]
    try:
        for arguments, message in guarded_calls:
            with pytest.raises(dataset_admission.DatasetAdmissionError, match=message):
                dataset_admission.admit_dataset(**arguments)
    finally:
        linked_root.unlink(missing_ok=True)

    with pytest.raises(TypeError, match="resources must contain only strings"):
        dataset_admission.admit_dataset(**{**_args(tmp_path), "resources": [1]})
    with pytest.raises(TypeError, match="description must be a string"):
        dataset_admission.admit_dataset(**{**_args(tmp_path), "description": 1})
    with pytest.raises(dataset_admission.DatasetAdmissionError, match="lower hard ceilings"):
        dataset_admission.AdmissionLimits(max_resources=0).validate()

    (tmp_path / "schema.json").write_text("[]", encoding="utf-8")
    with pytest.raises(dataset_admission.DatasetAdmissionError, match="JSON object"):
        dataset_admission.admit_dataset(**_args(tmp_path))
    (tmp_path / "schema.json").write_text("{}", encoding="utf-8")
    with pytest.raises(dataset_admission.DatasetAdmissionError, match="fields must be a list"):
        dataset_admission.admit_dataset(**_args(tmp_path))


@pytest.mark.parametrize(
    ("report", "message"),
    [
        (object(), "no structured report"),
        (_Report({"tasks": "bad"}), "tasks must be a list"),
        (
            _Report(
                {
                    "valid": True,
                    "tasks": [
                        {
                            "type": "file",
                            "valid": True,
                            "labels": [],
                            "stats": {"fields": 0, "rows": 0},
                            "errors": [],
                        }
                    ],
                    "errors": [],
                }
            ),
            "table task",
        ),
        (
            _Report(
                {
                    "valid": True,
                    "tasks": [
                        {
                            "type": "table",
                            "valid": True,
                            "labels": [],
                            "stats": {"fields": 0, "rows": 0},
                            "errors": [],
                        }
                    ],
                    "errors": [],
                }
            ),
            "schema field count",
        ),
        (
            _Report(
                {
                    "valid": True,
                    "tasks": [
                        {
                            "type": "table",
                            "valid": True,
                            "labels": [1],
                            "stats": {"fields": 1, "rows": 1},
                            "errors": [],
                        }
                    ],
                    "errors": [],
                }
            ),
            "malformed field labels",
        ),
        (
            _Report(
                {
                    "valid": True,
                    "tasks": [
                        {
                            "type": "table",
                            "valid": True,
                            "labels": ["id"],
                            "stats": "bad",
                            "errors": [],
                        }
                    ],
                    "errors": [],
                }
            ),
            "malformed task statistics",
        ),
    ],
)
def test_malformed_report_protocol_fails_closed(
    tmp_path: Path,
    report: object,
    message: str,
) -> None:
    _write_dataset(tmp_path)

    with pytest.raises(dataset_admission.DatasetAdmissionError, match=message):
        dataset_admission.admit_dataset(
            **_args(tmp_path),
            validation_runner=lambda *_args, **_kwargs: report,
        )


def test_check_mode_runs_read_only_native_validation_without_external_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dataset(tmp_path)
    before = sorted(path.name for path in tmp_path.iterdir())

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("dataset admission must not spawn or open a listener")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    result = execute_action(_args(tmp_path), check_mode=True)

    assert result["admitted"] is True
    assert result["check_mode"] is True
    assert result["changed"] is False
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_action_translates_admission_error_and_module_bypass_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dataset(tmp_path, invalid=True)
    action = object.__new__(ActionModule)
    with pytest.raises(dataset_admission.DatasetAdmissionError):
        action._execute(_args(tmp_path), check_mode=False)

    class Module:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["supports_check_mode"] is True
            assert kwargs["argument_spec"]["resources"]["type"] == "list"

        def fail_json(self, **kwargs: Any) -> None:
            raise RuntimeError(kwargs["msg"])

    monkeypatch.setattr(module_stub, "AnsibleModule", Module)
    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        module_stub.main()


def test_action_run_translates_failures_without_remote_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dataset(tmp_path, invalid=True)
    monkeypatch.setattr(action_plugin._ActionBase, "run", lambda *_args, **_kwargs: {})
    action = object.__new__(ActionModule)
    action._task = SimpleNamespace(args=_args(tmp_path), check_mode=True)

    result = action.run()

    assert result["failed"] is True
    assert result["changed"] is False
    assert result["admitted"] is False
    assert result["errors"]


def test_dataset_engineer_role_is_native_and_molecule_exercises_it() -> None:
    role = ROOT / "collections/ansible_collections/general_ludd/ai_ml/roles/dataset_engineer"
    defaults = yaml.safe_load((role / "defaults/main.yml").read_text(encoding="utf-8"))
    tasks_text = (role / "tasks/main.yml").read_text(encoding="utf-8")
    tasks = yaml.safe_load(tasks_text)
    converge = (ROOT / "molecule/playbooks/ai_ml_expert/default/converge.yml").read_text(
        encoding="utf-8"
    )
    verify = (ROOT / "molecule/playbooks/ai_ml_expert/default/verify.yml").read_text(encoding="utf-8")

    assert defaults["ai_ml_dataset_resources"] == []
    assert len(tasks) == 1
    assert "general_ludd.ai_ml.dataset_admit" in tasks[0]
    assert "service_request" not in tasks_text
    assert "ansible.builtin.uri" not in tasks_text
    assert "dataset_engineer" in converge
    assert "data_card_sha256" in verify
    assert "ignore_errors" not in converge + verify


def test_collection_boundary_and_execution_environment_pin_are_exact() -> None:
    collection_root = ROOT / "collections/ansible_collections/general_ludd/ai_ml"
    requirements = (ROOT / "config/ansible/requirements.txt").read_text(encoding="utf-8")
    controller = (ROOT / "requirements/profiles/ansible-controller/pyproject.toml").read_text(
        encoding="utf-8"
    )
    deptry = (ROOT / "config/deptry_profiles.toml").read_text(encoding="utf-8")
    runtime_tool = (ROOT / "scripts/ansible_runtime_artifacts.py").read_text(encoding="utf-8")

    assert scan_collections(collection_root) == []
    assert "frictionless==5.19.1" in requirements
    assert "frictionless==5.19.1" in controller
    assert '"frictionless"' in deptry
    assert '"frictionless"' in runtime_tool


def test_module_source_has_no_cli_network_or_process_escape() -> None:
    module_path = (
        ROOT
        / "collections/ansible_collections/general_ludd/ai_ml/plugins/module_utils/dataset_admission.py"
    )
    source = module_path.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "socket" not in source
    assert "urlopen" not in source
    assert "requests" not in source
    assert "httpx" not in source
    assert "frictionless validate" not in source
    assert "from frictionless import" in source


def test_module_stub_remains_executable_as_ansible_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    class Module:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def fail_json(self, **kwargs: Any) -> None:
            called.append(kwargs["msg"])

    monkeypatch.setattr(module_stub, "AnsibleModule", Module)
    runpy.run_path(str(Path(module_stub.__file__)), run_name="dataset_admit_import_only")
    module_stub.main()
    monkeypatch.setattr("ansible.module_utils.basic.AnsibleModule", Module)
    runpy.run_path(str(Path(module_stub.__file__)), run_name="__main__")
    assert called == [
        "dataset_admit requires its controller-side action plugin",
        "dataset_admit requires its controller-side action plugin",
    ]
