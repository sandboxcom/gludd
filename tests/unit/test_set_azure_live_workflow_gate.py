from __future__ import annotations

import importlib.util
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "set_azure_live_workflow_gate.py"
MAKEFILE = ROOT / "Makefile"
TARGET_CONTRACT = ROOT / "config" / "make_target_contract.json"


def _load_script() -> ModuleType:
    assert SCRIPT.is_file(), "the Azure live workflow gate controller must exist"
    spec = importlib.util.spec_from_file_location("set_azure_live_workflow_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _completed(
    args: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def _live_arguments(*, enabled: str = "1") -> list[str]:
    return [
        "--repository",
        "sandboxcom/gludd",
        "--enabled",
        enabled,
        "--validate-only",
        "0",
    ]


def test_validate_only_is_content_free_and_never_calls_github(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("validate-only must not call gh"),
    )

    result = module.main(
        [
            "--repository",
            "sandboxcom/gludd",
            "--enabled",
            "1",
            "--validate-only",
            "1",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "AZURE_LIVE_GATE_PLAN" in output
    assert "AZURE_CONTAINERAPP_LIVE_ENABLED" in output
    assert "enabled=true" in output


def test_live_change_reads_sets_and_verifies_without_printing_inventory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    calls: list[list[str]] = []
    inventories = iter(
        [
            [{"name": "EXISTING", "value": "do-not-log"}],
            [
                {"name": "EXISTING", "value": "do-not-log"},
                {"name": "AZURE_CONTAINERAPP_LIVE_ENABLED", "value": "true"},
            ],
        ]
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1:3] == ["variable", "list"]:
            return _completed(args, stdout=json.dumps(next(inventories)))
        return _completed(args)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert (
        module.main(
            [
                "--repository",
                "sandboxcom/gludd",
                "--enabled",
                "1",
                "--validate-only",
                "0",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "AZURE_LIVE_GATE_UPDATED" in output
    assert "do-not-log" not in output
    assert calls[1] == [
        "gh",
        "variable",
        "set",
        "AZURE_CONTAINERAPP_LIVE_ENABLED",
        "--repo",
        "sandboxcom/gludd",
        "--body",
        "true",
    ]


def test_verification_failure_restores_the_previous_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    calls: list[list[str]] = []
    inventories = iter(
        [
            [{"name": "AZURE_CONTAINERAPP_LIVE_ENABLED", "value": "false"}],
            [{"name": "AZURE_CONTAINERAPP_LIVE_ENABLED", "value": "wrong"}],
        ]
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1:3] == ["variable", "list"]:
            return _completed(args, stdout=json.dumps(next(inventories)))
        return _completed(args)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert (
        module.main(
            [
                "--repository",
                "sandboxcom/gludd",
                "--enabled",
                "1",
                "--validate-only",
                "0",
            ]
        )
        == 1
    )
    assert calls[-1][-1] == "false"


@pytest.mark.parametrize(
    "completed",
    [
        _completed([], returncode=1),
        _completed([], stdout="x" * 1_048_577),
        _completed([], stdout="{"),
        _completed([], stdout="{}"),
        _completed([], stdout="[1]"),
        _completed(
            [],
            stdout=json.dumps(
                [
                    {"name": "DUPLICATE", "value": "first"},
                    {"name": "DUPLICATE", "value": "second"},
                ]
            ),
        ),
    ],
)
def test_inventory_rejects_unusable_github_responses(
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(module, "_run_gh", lambda _arguments: completed)

    with pytest.raises(module._GithubVariableError):
        module._inventory("sandboxcom/gludd")


def test_run_gh_normalizes_process_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("sensitive detail")),
    )

    with pytest.raises(module._GithubVariableError, match="invocation failed"):
        module._run_gh(["variable", "list"])


def test_live_gate_is_idempotent_when_value_already_matches(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module,
        "_inventory",
        lambda _repository: {"AZURE_CONTAINERAPP_LIVE_ENABLED": "true"},
    )
    monkeypatch.setattr(
        module,
        "_set",
        lambda *args: pytest.fail("unchanged state must not be rewritten"),
    )

    assert module.main(_live_arguments()) == 0
    assert "AZURE_LIVE_GATE_UNCHANGED" in capsys.readouterr().out


def test_update_failure_before_mutation_requires_no_rollback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(module, "_inventory", lambda _repository: {})
    monkeypatch.setattr(
        module,
        "_set",
        lambda *args: (_ for _ in ()).throw(module._GithubVariableError()),
    )

    assert module.main(_live_arguments()) == 1
    assert "rollback=not_required" in capsys.readouterr().err


def test_failed_delete_is_reported_as_failed_rollback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    inventories: Iterator[dict[str, str]] = iter([{}, {}])
    monkeypatch.setattr(module, "_inventory", lambda _repository: next(inventories))
    monkeypatch.setattr(module, "_set", lambda *_args: None)
    monkeypatch.setattr(
        module,
        "_delete",
        lambda *args: (_ for _ in ()).throw(module._GithubVariableError()),
    )

    assert module.main(_live_arguments()) == 1
    assert "rollback=failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("repository", "enabled", "validate_only"),
    [
        ("not-a-repository", "1", "1"),
        ("sandboxcom/gludd", "yes", "1"),
        ("sandboxcom/gludd", "1", "yes"),
    ],
)
def test_rejects_invalid_arguments_before_calling_github(
    monkeypatch: pytest.MonkeyPatch,
    repository: str,
    enabled: str,
    validate_only: str,
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("invalid input must not call gh"),
    )

    assert (
        module.main(
            [
                "--repository",
                repository,
                "--enabled",
                enabled,
                "--validate-only",
                validate_only,
            ]
        )
        == 2
    )


def test_make_target_forwards_every_explicit_operator_input() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    marker = "azure-containerapp-live-workflow-gate:"
    assert marker in makefile
    recipe = makefile.split(marker, 1)[1].split("\n\n", 1)[0]
    assert "scripts/set_azure_live_workflow_gate.py" in recipe
    assert '--repository "$${_GLUDD_AZURE_LIVE_GATE_REPO_RAW}"' in recipe
    assert '--enabled "$${_GLUDD_AZURE_LIVE_GATE_ENABLED_RAW}"' in recipe
    assert '--validate-only "$${_GLUDD_AZURE_LIVE_GATE_VALIDATE_ONLY_RAW}"' in recipe


def test_make_contract_has_a_network_free_behavioral_example() -> None:
    contract = json.loads(TARGET_CONTRACT.read_text(encoding="utf-8"))
    target = next(
        item
        for item in contract["targets"]
        if item["name"] == "azure-containerapp-live-workflow-gate"
    )
    assert target["make_variables"] == [
        "AZURE_LIVE_GATE_REPO",
        "AZURE_LIVE_GATE_ENABLED",
        "AZURE_LIVE_GATE_VALIDATE_ONLY",
    ]
    assert "AZURE_LIVE_GATE_VALIDATE_ONLY=1" in target["behavior"]
