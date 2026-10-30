"""Native commit-tree comparison, literal inputs and read-only Make boundaries."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make") or "make"
GIT = shutil.which("git") or "git"
TARGET = "git-compare-commits"
BASE = "GIT_COMPARE_BASE"
HEAD = "GIT_COMPARE_HEAD"
DIFF_FLAGS = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--no-color", "--exit-code"]
PREFIX = ["--no-pager", "--no-optional-locks"]


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", BASE, HEAD,
        "GLUDD_GIT_COMPARE_BASE", "GLUDD_GIT_COMPARE_HEAD",
        "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
    ):
        environment.pop(name, None)
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "UV_OFFLINE": "1",
    })
    return environment


def _git(root: Path, *arguments: str, stdin: str | None = None) -> str:
    return subprocess.run(
        [GIT, *arguments], cwd=root, env=_environment(), input=stdin,
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()


@dataclass
class ComparisonRepository:
    root: Path
    environment: dict[str, str] = field(repr=False)
    first: str
    second: str
    same_tree: str
    blob: str
    tree: str
    tag: str

    def invoke(
        self, base: str, head: str, *, origin: str = "cli",
        makefile: str = "Makefile", extra_goals: tuple[str, ...] = (),
        cli_values: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = self.environment.copy()
        values = {BASE: base, HEAD: head}
        assignments: list[str] = []
        if origin == "cli":
            assignments = [f"{name}={value}" for name, value in values.items()]
        else:
            environment.update(values)
        if cli_values is not None:
            assignments.extend(f"{name}={value}" for name, value in cli_values.items())
        return subprocess.run(
            [
                MAKE, "--no-print-directory", *(["-e"] if origin == "env-e" else []),
                "-f", makefile, TARGET, *extra_goals, *assignments,
                f"PYTHON={sys.executable}", f"SYSTEM_PYTHON={sys.executable}",
                "GLUDD_XDIST_WORKERS=1",
            ],
            cwd=self.root, env=environment, capture_output=True, text=True,
            check=False, timeout=20,
        )

    def calls(self) -> list[list[str]]:
        path = self.root / "native-git-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def snapshot(self) -> tuple[bytes, int, int, int, bytes, tuple[tuple[str, bytes], ...], bytes]:
        index = self.root / ".git/index"
        metadata = index.stat()
        refs = tuple(
            (str(path.relative_to(self.root)), path.read_bytes())
            for path in sorted((self.root / ".git/refs").rglob("*")) if path.is_file()
        )
        return (
            index.read_bytes(), metadata.st_mtime_ns, stat.S_IMODE(metadata.st_mode),
            metadata.st_ino, (self.root / ".git/HEAD").read_bytes(), refs,
            (self.root / "owned.txt").read_bytes(),
        )


def test_fixture_repr_omits_controlled_environment() -> None:
    controlled = {"SYNTHETIC_TOKEN": "test-only-repr-value", "UNRELATED_CARRIER": "retained"}
    fixture = ComparisonRepository(
        root=Path("synthetic fixture"), environment=controlled,
        first="0" * 40, second="1" * 40, same_tree="2" * 40,
        blob="3" * 40, tree="4" * 40, tag="5" * 40,
    )
    representation = repr(fixture)
    assert "environment=" not in representation
    assert "SYNTHETIC_TOKEN" not in representation
    assert "test-only-repr-value" not in representation
    assert fixture.environment is controlled
    assert fixture.environment["UNRELATED_CARRIER"] == "retained"


@pytest.fixture
def comparison_repo(tmp_path: Path) -> ComparisonRepository:
    root = tmp_path / "comparison repository"
    root.mkdir()
    _git(root, "init", "--quiet", "-b", "fixture")
    _git(root, "config", "user.name", "Commit comparison fixture")
    _git(root, "config", "user.email", "comparison@example.invalid")
    _git(root, "config", "core.hooksPath", os.devnull)
    _git(root, "config", "commit.gpgsign", "false")
    (root / "owned.txt").write_text("older committed bytes\n", encoding="utf-8")
    _git(root, "add", "owned.txt")
    _git(root, "commit", "--quiet", "-m", "older")
    first = _git(root, "rev-parse", "HEAD")
    (root / "owned.txt").write_text("newer committed bytes\n", encoding="utf-8")
    _git(root, "commit", "--quiet", "-am", "newer")
    second = _git(root, "rev-parse", "HEAD")
    _git(root, "commit", "--quiet", "--allow-empty", "-m", "same tree distinct commit")
    same_tree = _git(root, "rev-parse", "HEAD")
    blob = _git(root, "hash-object", "-w", "--stdin", stdin="private blob\n")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    _git(root, "tag", "-a", "typed-tag", "-m", "private annotated tag")
    tag = _git(root, "rev-parse", "typed-tag")
    (root / "owned.txt").write_text("staged bytes must survive\n", encoding="utf-8")
    _git(root, "add", "owned.txt")
    (root / "owned.txt").write_text("unstaged bytes must survive\n", encoding="utf-8")
    shutil.copyfile(ROOT / "Makefile", root / "Makefile")
    (root / "make").symlink_to(ROOT / "make", target_is_directory=True)
    (root / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    tools = root / "native-tools"
    tools.mkdir()
    git_adapter = tools / "git"
    git_adapter.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with Path(os.environ['COMPARE_CALLS']).open('a') as output:\n"
        "    output.write(json.dumps(args) + '\\n')\n"
        "if 'diff' in args and os.environ.get('COMPARE_DIFF_FAILURE'):\n"
        "    print('native fixture diff failure', file=sys.stderr)\n"
        "    raise SystemExit(int(os.environ['COMPARE_DIFF_FAILURE']))\n"
        f"os.execve({GIT!r}, [{GIT!r}, *args], os.environ.copy())\n",
        encoding="utf-8",
    )
    git_adapter.chmod(0o700)
    infocmp = tools / "infocmp"
    infocmp.write_text(
        f"#!{sys.executable}\n"
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['COMPARE_STARTUP']).write_text('startup executed\\n')\n",
        encoding="utf-8",
    )
    infocmp.chmod(0o700)
    environment = _environment()
    environment.update({
        "PATH": str(tools) + os.pathsep + environment.get("PATH", ""),
        "COMPARE_CALLS": str(root / "native-git-calls.jsonl"),
        "COMPARE_STARTUP": str(root / "startup-seen"),
        "COMPARE_CARRIER": "preserve unrelated environment",
    })
    return ComparisonRepository(root, environment, first, second, same_tree, blob, tree, tag)


@pytest.mark.parametrize("origin", ("cli", "env", "env-e"))
@pytest.mark.parametrize("relationship", ("same", "equal-tree", "different"))
def test_native_commit_tree_result_and_index_refs_remain_unchanged(
    comparison_repo: ComparisonRepository, origin: str, relationship: str,
) -> None:
    fixture = comparison_repo
    head = fixture.first if relationship == "same" else (
        fixture.same_tree if relationship == "equal-tree" else fixture.second
    )
    base = fixture.second if relationship == "equal-tree" else fixture.first
    expected = subprocess.run(
        [GIT, *PREFIX, *DIFF_FLAGS, base, head, "--"], cwd=fixture.root,
        env=_environment(), capture_output=True, text=True, check=False, timeout=15,
    )
    before = fixture.snapshot()
    result = fixture.invoke(base, head, origin=origin)
    assert expected.returncode == (1 if relationship == "different" else 0)
    assert result.returncode == (2 if expected.returncode else 0), result.stdout + result.stderr
    assert result.stdout == expected.stdout
    outcome = "different" if expected.returncode else "identical"
    assert f"outcome={outcome} native_status={expected.returncode}" in result.stderr
    assert f"base={base} head={head} scope=commit-trees-only" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert fixture.calls() == [
        [*PREFIX, "cat-file", "-t", base], [*PREFIX, "cat-file", "-t", head],
        [*PREFIX, *DIFF_FLAGS, base, head, "--"],
    ]
    assert fixture.snapshot() == before
    assert fixture.environment["COMPARE_CARRIER"] == "preserve unrelated environment"
    if expected.returncode:
        assert "Error 1" in result.stderr
        assert "older committed bytes" in result.stdout and "newer committed bytes" in result.stdout
    else:
        assert result.stdout == ""


@pytest.mark.parametrize("origin", ("env", "env-e"))
def test_command_line_values_preserve_native_precedence(
    comparison_repo: ComparisonRepository, origin: str,
) -> None:
    fixture = comparison_repo
    result = fixture.invoke(
        fixture.first, fixture.second, origin=origin,
        cli_values={BASE: fixture.second, HEAD: fixture.second},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert f"base={fixture.second} head={fixture.second}" in result.stderr


@pytest.mark.parametrize("field", (BASE, HEAD))
@pytest.mark.parametrize(
    "value", ("", "HEAD", "a" * 39, "a" * 41, "g" * 40, "a" * 20 + " " + "a" * 20, "a" * 40 + "\n"),
)
def test_non_full_hex_inputs_refuse_before_startup_or_git(
    comparison_repo: ComparisonRepository, field: str, value: str,
) -> None:
    fixture = comparison_repo
    values = {BASE: fixture.first, HEAD: fixture.second, field: value}
    before = fixture.snapshot()
    result = fixture.invoke(values[BASE], values[HEAD], origin="env-e")
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert "outcome=refused" in result.stderr and field in result.stderr
    assert "full40hex" in result.stderr
    assert fixture.calls() == []
    assert not (fixture.root / "startup-seen").exists()
    assert fixture.snapshot() == before


@pytest.mark.parametrize("field", (BASE, HEAD))
@pytest.mark.parametrize("origin", ("cli", "env", "env-e"))
@pytest.mark.parametrize("evaluation", ("make", "backtick"))
def test_active_same_origin_witness_then_guarded_no_evaluation(
    comparison_repo: ComparisonRepository, field: str, origin: str, evaluation: str,
) -> None:
    fixture = comparison_repo
    marker = fixture.root / "owned witness marker"
    command = "printf active-witness > " + shlex.quote(str(marker))
    payload = f"$(shell {command})" if evaluation == "make" else f"\x60{command}\x60"
    witness = fixture.root / "unguarded-witness.mk"
    witness.write_text(
        f'{TARGET}:\n\t@printf "%s\\n" "$({field})" >/dev/null\n', encoding="utf-8",
    )
    values = {BASE: fixture.first, HEAD: fixture.second, field: payload}
    active = fixture.invoke(values[BASE], values[HEAD], origin=origin, makefile=witness.name)
    assert active.returncode == 0, active.stdout + active.stderr
    assert marker.read_text() == "active-witness"
    marker.unlink()
    before = fixture.snapshot()
    result = fixture.invoke(values[BASE], values[HEAD], origin=origin)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == "" and "outcome=refused" in result.stderr
    assert not marker.exists()
    assert fixture.calls() == [] and not (fixture.root / "startup-seen").exists()
    assert fixture.snapshot() == before


@pytest.mark.parametrize("field", (BASE, HEAD))
@pytest.mark.parametrize("kind", ("missing", "blob", "tree", "tag"))
def test_missing_or_noncommit_objects_keep_native_error_and_no_diff(
    comparison_repo: ComparisonRepository, field: str, kind: str,
) -> None:
    fixture = comparison_repo
    value = "f" * 40 if kind == "missing" else getattr(fixture, kind)
    values = {BASE: fixture.first, HEAD: fixture.second, field: value}
    before = fixture.snapshot()
    result = fixture.invoke(values[BASE], values[HEAD])
    assert result.returncode == 2 and result.stdout == "", result.stdout + result.stderr
    assert f"input={field}" in result.stderr
    if kind == "missing":
        assert "outcome=failed native_status=128" in result.stderr
        assert "Error 128" in result.stderr
    else:
        assert "outcome=refused native_status=2" in result.stderr
        assert "phase=object-type" in result.stderr
    assert not any("diff" in row for row in fixture.calls())
    assert fixture.snapshot() == before


def test_native_diff_error_is_preserved_without_no_diff_success(
    comparison_repo: ComparisonRepository,
) -> None:
    fixture = comparison_repo
    fixture.environment["COMPARE_DIFF_FAILURE"] = "37"
    before = fixture.snapshot()
    result = fixture.invoke(fixture.first, fixture.second)
    assert result.returncode == 2 and result.stdout == ""
    assert "native fixture diff failure" in result.stderr
    assert "outcome=failed native_status=37" in result.stderr and "Error 37" in result.stderr
    assert "outcome=identical" not in result.stderr and "No diff" not in result.stdout
    assert fixture.snapshot() == before


def test_commit_comparison_refuses_multiple_goals_before_startup(
    comparison_repo: ComparisonRepository,
) -> None:
    fixture = comparison_repo
    result = fixture.invoke(fixture.first, fixture.second, extra_goals=("help",))
    assert result.returncode == 2 and result.stdout == ""
    assert "one-goal" in result.stderr and "outcome=refused" in result.stderr
    assert not (fixture.root / "startup-seen").exists() and fixture.calls() == []


def test_native_comparison_never_runs_external_diff_or_textconv(
    comparison_repo: ComparisonRepository,
) -> None:
    fixture = comparison_repo
    marker = fixture.root / "forbidden-external-tool"
    tool = fixture.root / "external-diff"
    tool.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('forbidden')\nraise SystemExit(79)\n",
        encoding="utf-8",
    )
    tool.chmod(0o700)
    _git(fixture.root, "config", "diff.external", str(tool))
    _git(fixture.root, "config", "diff.owned.textconv", str(tool))
    (fixture.root / ".gitattributes").write_text("owned.txt diff=owned\n", encoding="utf-8")
    before = fixture.snapshot()
    result = fixture.invoke(fixture.first, fixture.second)
    assert result.returncode == 2 and "outcome=different native_status=1" in result.stderr
    assert "-older committed bytes" in result.stdout and "+newer committed bytes" in result.stdout
    assert not marker.exists() and fixture.snapshot() == before


@pytest.mark.parametrize("origin", ("cli", "env", "env-e"))
def test_native_stdin_refuses_implicit_cwd_import_shadow_with_carrier(
    comparison_repo: ComparisonRepository, origin: str,
) -> None:
    fixture = comparison_repo
    carrier = fixture.root.parent / "unrelated-pythonpath-carrier"
    carrier.mkdir()
    receipt = fixture.root / "stdin-carrier-receipt"
    marker = fixture.root / "owned-stdin-shadow-marker"
    (carrier / "sitecustomize.py").write_text(
        "import os, sys\n"
        "if sys.argv == ['-']:\n"
        "    with open(os.environ['COMPARE_STDIN_CARRIER_RECEIPT'], 'a', encoding='utf-8') as output:\n"
        "        output.write(str(sys.flags.safe_path) + '\\t' + os.environ['PYTHONPATH'] + '\\n')\n",
        encoding="utf-8",
    )
    fixture.environment["PYTHONPATH"] = str(carrier)
    fixture.environment["COMPARE_STDIN_CARRIER_RECEIPT"] = str(receipt)
    fixture.environment["COMPARE_STDIN_SHADOW_MARKER"] = str(marker)
    (fixture.root / "subprocess.py").write_text(
        "import os\n"
        "with open(os.environ['COMPARE_STDIN_SHADOW_MARKER'], 'w', encoding='utf-8') as output:\n"
        "    output.write('executed native subprocess shadow\\n')\n"
        "raise SystemExit(73)\n",
        encoding="utf-8",
    )
    owner = (ROOT / "make/20-recovery-and-git.mk").read_text(encoding="utf-8")
    recipe = owner.split("\ngit-compare-commits:\n", 1)[1].split("\n\n", 1)[0]
    assert recipe.endswith(' | "$(PYTHON)" -') or recipe.endswith(' | "$(PYTHON)" -P -')
    unguarded_recipe = recipe.replace(' | "$(PYTHON)" -P -', ' | "$(PYTHON)" -')
    witness = fixture.root / "unguarded-native-stdin.mk"
    witness.write_text(
        f"include {ROOT / 'make/00-foundation.mk'}\n{TARGET}:\n{unguarded_recipe}\n",
        encoding="utf-8",
    )
    before = fixture.snapshot()

    active = fixture.invoke(fixture.first, fixture.first, origin=origin, makefile=witness.name)
    assert active.returncode == 2 and "Error 73" in active.stderr
    assert marker.read_text() == "executed native subprocess shadow\n"
    assert receipt.read_text().splitlines() == ["False\t" + str(carrier)]
    assert fixture.calls() == []
    marker.unlink()

    guarded = fixture.invoke(fixture.first, fixture.first, origin=origin)
    assert not marker.exists(), "The actual stdin bootstrap executed its implicit CWD shadow"
    assert guarded.returncode == 0, guarded.stdout + guarded.stderr
    assert guarded.stdout == "" and "outcome=identical native_status=0" in guarded.stderr
    assert receipt.read_text().splitlines() == ["False\t" + str(carrier), "True\t" + str(carrier)]
    assert fixture.environment["PYTHONPATH"] == str(carrier)
    assert fixture.environment["COMPARE_CARRIER"] == "preserve unrelated environment"
    assert fixture.snapshot() == before


@pytest.mark.parametrize("origin", ("cli", "env", "env-e"))
@pytest.mark.parametrize("candidate_carrier", ("absent", "empty", "pinned"))
def test_comparison_does_not_capture_implicit_gate_head(
    comparison_repo: ComparisonRepository,
    origin: str,
    candidate_carrier: str,
) -> None:
    """Explicit immutable inputs must not depend on a copied gate candidate."""
    fixture = comparison_repo
    candidate = None if candidate_carrier == "absent" else (fixture.second if candidate_carrier == "pinned" else "")
    if candidate is None:
        fixture.environment.pop("GATE_CANDIDATE_SHA", None)
    else:
        fixture.environment["GATE_CANDIDATE_SHA"] = candidate
    before = fixture.snapshot()

    result = fixture.invoke(fixture.first, fixture.first, origin=origin)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert "outcome=identical native_status=0" in result.stderr
    assert fixture.calls() == [
        [*PREFIX, "cat-file", "-t", fixture.first],
        [*PREFIX, "cat-file", "-t", fixture.first],
        [*PREFIX, *DIFF_FLAGS, fixture.first, fixture.first, "--"],
    ]
    assert fixture.snapshot() == before
    assert fixture.environment.get("GATE_CANDIDATE_SHA") == candidate
    assert fixture.environment["COMPARE_CARRIER"] == "preserve unrelated environment"


@pytest.mark.parametrize("environment_override", (False, True))
@pytest.mark.parametrize("candidate_carrier", ("absent", "empty", "pinned"))
def test_noncomparison_gate_config_retains_native_candidate_capture(
    comparison_repo: ComparisonRepository,
    candidate_carrier: str,
    environment_override: bool,
) -> None:
    """The ordinary native gate owner still captures HEAD or retains its carrier."""
    fixture = comparison_repo
    environment = fixture.environment.copy()
    candidate = None if candidate_carrier == "absent" else (fixture.second if candidate_carrier == "pinned" else "")
    if candidate is None:
        environment.pop("GATE_CANDIDATE_SHA", None)
    else:
        environment["GATE_CANDIDATE_SHA"] = candidate
    environment.pop("GATE_PREFLIGHT_TARGETS", None)
    before = fixture.snapshot()

    result = subprocess.run(
        [
            MAKE,
            "--no-print-directory",
            *(["-e"] if environment_override else []),
            "gate-admission-config",
            f"PYTHON={sys.executable}",
            f"SYSTEM_PYTHON={sys.executable}",
            "GLUDD_XDIST_WORKERS=1",
            "GATE_TEST_PROFILE=release",
            "GATE_EXACT_SHA_RESUME=0",
            "GATE_MAX_FILES_PER_BATCH=16",
            "GATE_BATCH_WORKERS=1",
            "PYTEST_CMD=",
            "PYTEST_ARGS=",
            "PYTEST_ADDOPTS=",
            "ALLOW_DIRTY_CI_REPLICA=0",
            "ALLOW_DIRTY_FOCUSED_REPRO=0",
            "DUAL_TRACK_LOCAL_VALIDATE_ONLY=0",
            "MYPY_MAX=0",
        ],
        cwd=fixture.root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "GATE-ADMISSION-CONFIG profile=release exact_sha_resume=0 max_files_per_batch=16 workers=1 coverage=85/75"
    ) in result.stdout
    assert fixture.calls() == ([] if candidate_carrier == "pinned" else [["rev-parse", "HEAD"]])
    assert fixture.snapshot() == before
    assert environment.get("GATE_CANDIDATE_SHA") == candidate
    assert environment["COMPARE_CARRIER"] == "preserve unrelated environment"
