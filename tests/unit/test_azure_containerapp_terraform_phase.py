"""Non-destructive tests for the app-only Terraform phase executor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO, Any

import pytest
import scripts.azure_containerapp_terraform_phase as terraform_phase
from scripts.azure_containerapp_terraform_phase import main


class _Process:
    def __init__(
        self,
        argv: list[str],
        *,
        cwd: str,
        stdout: IO[str] | None = None,
        returncode: int = 0,
        polls_before_exit: int = 0,
        **kwargs: object,
    ) -> None:
        self.argv = argv
        self.cwd = cwd
        self.kwargs = kwargs
        self.returncode = returncode
        self.polls_before_exit = polls_before_exit
        self.polls = 0
        self.terminated = False
        self.killed = False
        if hasattr(stdout, "write"):
            stdout.write('{"safe":true}')
            stdout.flush()

    def poll(self) -> int | None:
        self.polls += 1
        if self.polls <= self.polls_before_exit:
            return None
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def _root(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    allowed = tmp_path / "gludd-azure-containerapp-live-proof"
    terraform_dir = allowed / ("a" * 24)
    terraform_dir.mkdir(parents=True)
    for name in ("main.tf", "variables.tf", "outputs.tf"):
        (terraform_dir / name).write_text("# bounded\n", encoding="utf-8")
    marker = {
        "protocol": "gludd-azure-containerapp-live-proof-v1",
        "operation_digest": "a" * 64,
    }
    (terraform_dir / ".gludd-azure-containerapp-live-proof.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )
    plan_file = terraform_dir / "gludd.tfplan"
    json_file = terraform_dir / "gludd.plan.json"
    return allowed, terraform_dir, plan_file, json_file


def _run(
    tmp_path: Path,
    phase: str,
    *,
    process_factory: Any,
) -> tuple[int, Path, Path]:
    allowed, terraform_dir, plan_file, json_file = _root(tmp_path)
    if phase in {"apply", "show-plan"}:
        plan_file.write_bytes(b"saved-plan")
    result = main(
        [
            "--phase",
            phase,
            "--terraform-dir",
            str(terraform_dir),
            "--plan-file",
            str(plan_file),
            "--json-file",
            str(json_file),
        ],
        allowed_root=allowed,
        binary_resolver=lambda: "/opt/gludd/bin/terraform",
        process_factory=process_factory,
        heartbeat_seconds=0.001,
        poll_seconds=0.001,
    )
    return result, plan_file, json_file


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("init", ["init", "-backend=false", "-input=false", "-no-color"]),
        ("validate", ["validate", "-no-color"]),
        (
            "plan",
            ["plan", "-input=false", "-no-color", "-out={plan}"],
        ),
        ("show-plan", ["show", "-json", "{plan}"]),
        (
            "apply",
            ["apply", "-input=false", "-no-color", "-auto-approve", "{plan}"],
        ),
        ("output", ["output", "-json"]),
        (
            "destroy",
            ["destroy", "-input=false", "-no-color", "-auto-approve"],
        ),
    ],
)
def test_each_phase_uses_one_list_argv_without_a_shell(
    tmp_path: Path,
    phase: str,
    expected: list[str],
) -> None:
    processes: list[_Process] = []

    def factory(argv: list[str], **kwargs: object) -> _Process:
        process = _Process(argv, **cast_kwargs(kwargs))
        processes.append(process)
        return process

    result, plan_file, json_file = _run(
        tmp_path,
        phase,
        process_factory=factory,
    )

    assert result == 0
    assert len(processes) == 1
    rendered_expected = [
        value.format(plan=str(plan_file)) for value in expected
    ]
    assert processes[0].argv == ["/opt/gludd/bin/terraform", *rendered_expected]
    assert processes[0].kwargs["shell"] is False
    assert processes[0].cwd == str(plan_file.parent)
    if phase in {"show-plan", "output"}:
        assert json.loads(json_file.read_text(encoding="utf-8")) == {"safe": True}
        assert oct(json_file.stat().st_mode & 0o777) == "0o600"


def cast_kwargs(values: dict[str, object]) -> dict[str, Any]:
    return values


@pytest.mark.parametrize(
    ("phase", "remove_name"),
    [
        ("init", "main.tf"),
        ("validate", "variables.tf"),
        ("plan", ".gludd-azure-containerapp-live-proof.json"),
        ("apply", "gludd.tfplan"),
    ],
)
def test_missing_owned_artifact_refuses_before_subprocess(
    tmp_path: Path,
    phase: str,
    remove_name: str,
) -> None:
    allowed, terraform_dir, plan_file, json_file = _root(tmp_path)
    if phase == "apply":
        plan_file.write_bytes(b"saved-plan")
    (terraform_dir / remove_name).unlink()

    result = main(
        [
            "--phase",
            phase,
            "--terraform-dir",
            str(terraform_dir),
            "--plan-file",
            str(plan_file),
            "--json-file",
            str(json_file),
        ],
        allowed_root=allowed,
        binary_resolver=lambda: "/opt/gludd/bin/terraform",
        process_factory=lambda *_args, **_kwargs: pytest.fail(
            "subprocess must not start"
        ),
    )

    assert result == 2


def test_destroy_cannot_target_a_directory_outside_the_owned_root(
    tmp_path: Path,
) -> None:
    allowed, _terraform_dir, _plan_file, _json_file = _root(tmp_path)
    outside = tmp_path / "unrelated-terraform"
    outside.mkdir()
    for name in ("main.tf", "variables.tf", "outputs.tf"):
        (outside / name).write_text("# unrelated\n", encoding="utf-8")
    marker = outside / ".gludd-azure-containerapp-live-proof.json"
    marker.write_text(
        json.dumps(
            {
                "protocol": "gludd-azure-containerapp-live-proof-v1",
                "operation_digest": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--phase",
            "destroy",
            "--terraform-dir",
            str(outside),
            "--plan-file",
            str(outside / "gludd.tfplan"),
            "--json-file",
            str(outside / "gludd.output.json"),
        ],
        allowed_root=allowed,
        binary_resolver=lambda: "/opt/gludd/bin/terraform",
        process_factory=lambda *_args, **_kwargs: pytest.fail(
            "subprocess must not start"
        ),
    )

    assert result == 2
    assert marker.exists()


def test_phase_failure_is_censored_and_does_not_publish_partial_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result, _plan_file, json_file = _run(
        tmp_path,
        "output",
        process_factory=lambda argv, **kwargs: _Process(
            argv,
            returncode=1,
            **cast_kwargs(kwargs),
        ),
    )

    captured = capsys.readouterr()
    assert result == 2
    assert not json_file.exists()
    assert "AZURE_CONTAINERAPP_TERRAFORM_INVALID phase=output" in captured.err
    assert "clientSecret" not in captured.out + captured.err


def test_long_phase_emits_content_free_heartbeats(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result, _plan_file, _json_file = _run(
        tmp_path,
        "destroy",
        process_factory=lambda argv, **kwargs: _Process(
            argv,
            polls_before_exit=3,
            **cast_kwargs(kwargs),
        ),
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "state=started" in captured.out
    assert "state=heartbeat" in captured.out
    assert "state=succeeded" in captured.out
    assert SECRET_NOT_PRESENT not in captured.out


@pytest.mark.parametrize(
    ("heartbeat_seconds", "poll_seconds", "binary"),
    [
        (0.0, 0.25, "/opt/gludd/bin/terraform"),
        (10.0, 0.0, "/opt/gludd/bin/terraform"),
        (10.0, 0.25, ""),
        (10.0, 0.25, None),
    ],
    ids=("heartbeat-zero", "poll-zero", "empty-binary", "non-string-binary"),
)
def test_phase_refuses_invalid_timing_and_binary_boundaries_before_subprocess(
    tmp_path: Path,
    heartbeat_seconds: float,
    poll_seconds: float,
    binary: object,
) -> None:
    allowed, terraform_dir, plan_file, json_file = _root(tmp_path)

    result = main(
        [
            "--phase",
            "init",
            "--terraform-dir",
            str(terraform_dir),
            "--plan-file",
            str(plan_file),
            "--json-file",
            str(json_file),
        ],
        allowed_root=allowed,
        binary_resolver=lambda: binary,
        process_factory=lambda *_args, **_kwargs: pytest.fail(
            "subprocess must not start"
        ),
        heartbeat_seconds=heartbeat_seconds,
        poll_seconds=poll_seconds,
    )

    assert result == 2


def test_wrapper_leaves_optional_executor_dependencies_at_production_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed, terraform_dir, plan_file, json_file = _root(tmp_path)
    constructed: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []

    class Executor:
        def __init__(self, **kwargs: object) -> None:
            constructed.append(dict(kwargs))

        def run(self, **kwargs: object) -> None:
            runs.append(dict(kwargs))

    monkeypatch.setattr(
        terraform_phase,
        "AzureContainerAppTerraformPhaseExecutor",
        Executor,
    )

    result = main(
        [
            "--phase",
            "init",
            "--terraform-dir",
            str(terraform_dir),
            "--plan-file",
            str(plan_file),
            "--json-file",
            str(json_file),
        ],
        allowed_root=allowed,
    )

    assert result == 0
    assert "binary_resolver" not in constructed[0]
    assert "process_factory" not in constructed[0]
    assert runs[0]["phase"] == "init"


@pytest.mark.parametrize(
    "marker",
    [
        b"",
        b"x" * 4097,
        b"[]",
        b'{"protocol":"wrong","operation_digest":"' + b"a" * 64 + b'"}',
        b'{"protocol":"gludd-azure-containerapp-live-proof-v1",'
        b'"operation_digest":7}',
        b'{"protocol":"gludd-azure-containerapp-live-proof-v1",'
        b'"operation_digest":"' + b"b" * 64 + b'"}',
    ],
    ids=(
        "empty",
        "oversized",
        "non-object",
        "wrong-protocol",
        "non-string-digest",
        "wrong-directory-prefix",
    ),
)
def test_ownership_marker_rejects_ambiguous_or_unbound_content(
    tmp_path: Path,
    marker: bytes,
) -> None:
    allowed, terraform_dir, plan_file, json_file = _root(tmp_path)
    (terraform_dir / ".gludd-azure-containerapp-live-proof.json").write_bytes(marker)

    result = main(
        [
            "--phase",
            "destroy",
            "--terraform-dir",
            str(terraform_dir),
            "--plan-file",
            str(plan_file),
            "--json-file",
            str(json_file),
        ],
        allowed_root=allowed,
        binary_resolver=lambda: "/opt/gludd/bin/terraform",
        process_factory=lambda *_args, **_kwargs: pytest.fail(
            "subprocess must not start"
        ),
    )

    assert result == 2


def test_regular_file_can_treat_an_absent_optional_artifact_as_valid(
    tmp_path: Path,
) -> None:
    assert terraform_phase._regular_file(tmp_path / "absent", required=False) is True


SECRET_NOT_PRESENT = "a-secret-that-must-never-appear"


def test_script_is_only_a_wrapper_around_the_production_executor() -> None:
    source = Path(terraform_phase.__file__).read_text(encoding="utf-8")

    assert "AzureContainerAppTerraformPhaseExecutor" in source
    assert "subprocess.Popen" not in source
    assert "def _command(" not in source


def test_make_target_is_explicit_validate_only_and_contract_tracked() -> None:
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    contract = json.loads(
        (root / "config/make_target_contract.json").read_text(encoding="utf-8")
    )

    assert "azure-containerapp-terraform-phase:" in makefile
    assert "scripts/azure_containerapp_terraform_phase.py" in makefile
    assert "AZURE_CONTAINERAPP_TF_VALIDATE_ONLY" in makefile
    entry = next(
        target
        for target in contract["targets"]
        if target["name"] == "azure-containerapp-terraform-phase"
    )
    assert entry["make_variables"] == [
        "AZURE_CONTAINERAPP_TF_PHASE",
        "AZURE_CONTAINERAPP_TF_DIR",
        "AZURE_CONTAINERAPP_TF_PLAN_FILE",
        "AZURE_CONTAINERAPP_TF_JSON_FILE",
        "AZURE_CONTAINERAPP_TF_VALIDATE_ONLY",
    ]
    assert "AZURE_CONTAINERAPP_TF_VALIDATE_ONLY=1" in entry["behavior"]
