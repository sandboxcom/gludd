"""Behavioral regression for exact frozen data and early formatter admission."""

from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / ".pre-commit-config.yaml"
SHIPPING_PATH = ROOT / "make/40-cross-version-and-worktrees.mk"
FIXTURE_PATH = "tests/fixtures/ansible_ee_searxng/limiter.toml.txt"


def _selected(hook_id: str, names: list[str]) -> list[str]:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    hook = next(hook for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == hook_id)
    selector = cast(
        Callable[[list[str], str, str], Iterator[str]],
        import_module("pre_commit.commands.run").filter_by_include_exclude,
    )
    return list(selector(names, hook.get("files", ""), hook.get("exclude", "^$")))


@pytest.mark.parametrize("hook_id", ("trailing-whitespace", "end-of-file-fixer"))
def test_only_exact_frozen_fixture_is_excluded(hook_id: str) -> None:
    ordinary = [
        "src/general_ludd/example.py",
        "scripts/example.py",
        "tests/unit/test_example.py",
        "tests/fixtures/ansible_ee_searxng/other.txt",
        FIXTURE_PATH + ".py",
        "nested/" + FIXTURE_PATH,
        "docs/example.md",
        "config/example.json",
    ]
    assert _selected(hook_id, [FIXTURE_PATH, *ordinary]) == ordinary
    assert _selected(hook_id, ["docs/presentation/deck/vendor/reveal.js"]) == []
    if hook_id == "end-of-file-fixer":
        assert _selected(hook_id, ["config/self-improve/catalog-truth.json"]) == []


@pytest.mark.parametrize("failing_hook", (None, "trailing-whitespace", "end-of-file-fixer", "detect-secrets-readonly"))
@pytest.mark.parametrize("inherited_index", (None, "https://private.invalid/simple"))
@pytest.mark.parametrize("environment_override", (False, True))
def test_existing_guard_routes_formatter_failure_before_later_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_hook: str | None,
    inherited_index: str | None,
    environment_override: bool,
) -> None:
    if inherited_index is None:
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    else:
        monkeypatch.setenv("PIP_INDEX_URL", inherited_index)
    inherited = os.environ.copy()
    root, _ = _native_workspace(tmp_path)
    filename = "owned staged file.txt"
    (root / filename).write_text("owned staged input\n", encoding="utf-8")
    _native_git(root, "add", "--", filename)
    trace = tmp_path / "trace.jsonl"
    stub = tmp_path / "native_tool_boundary.py"
    stub.write_text(
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['run', '--active']:\n"
        "    args = ['run'] + args[2:]\n"
        "if args[:2] == ['run', 'python']:\n"
        "    os.execve(sys.executable, [sys.executable, *args[2:]], dict(os.environ))\n"
        f"trace = pathlib.Path({str(trace)!r})\n"
        "with trace.open('a') as stream:\n"
        "    stream.write(json.dumps(dict(argv=args, index=os.environ.get('PIP_INDEX_URL'))) + '\\n')\n"
        f"raise SystemExit(1 if args[3] == {failing_hook!r} else 0)\n",
        encoding="utf-8",
    )
    result = _native_guard(root, stub, environment_override=environment_override)
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    hooks = ["trailing-whitespace", "end-of-file-fixer", "detect-secrets-readonly"]
    expected = hooks if failing_hook is None else hooks[: hooks.index(failing_hook) + 1]
    assert [call["argv"] for call in calls] == [
        ["run", "pre-commit", "run", hook, "--files", filename] for hook in expected
    ]
    assert [call["index"] for call in calls] == ["https://pypi.org/simple"] * len(expected)
    assert (result.returncode == 0) is (failing_hook is None)
    assert os.environ == inherited


NATIVE_HOOKS = ("trailing-whitespace", "end-of-file-fixer", "detect-secrets-readonly")


def _native_environment() -> dict[str, str]:
    excluded = {
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEOVERRIDES",
        "MAKELEVEL",
        "PYTEST_ARGS",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
    }
    environment = {key: value for key, value in os.environ.items() if key not in excluded}
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return environment


def _native_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("git")
    assert executable is not None
    return subprocess.run(
        [executable, *arguments],
        cwd=root,
        env=_native_environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )


def _native_config(root: Path) -> None:
    """Retain the real pinned formatter hooks and real readonly secret owner."""
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    upstream = next(repo for repo in config["repos"] if repo["repo"].endswith("/pre-commit-hooks"))
    formatters = [hook for hook in upstream["hooks"] if hook["id"] in NATIVE_HOOKS[:2]]
    secret = next(
        dict(hook)
        for repo in config["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
        if hook["id"] == NATIVE_HOOKS[2]
    )
    secret["entry"] = shlex.join(
        (
            sys.executable,
            str(ROOT / "scripts/detect_secrets_readonly.py"),
            "--baseline",
            str(ROOT / ".secrets.baseline"),
        )
    )
    (root / ".pre-commit-config.yaml").write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {"repo": upstream["repo"], "rev": upstream["rev"], "hooks": formatters},
                    {"repo": "local", "hooks": [secret]},
                ]
            }
        ),
        encoding="utf-8",
    )


def _native_workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "native-guard"
    root.mkdir()
    _native_git(root, "init", "-b", "base")
    _native_git(root, "config", "user.email", "guard-fixture@example.invalid")
    _native_git(root, "config", "user.name", "Gludd staged guard fixture")
    _native_config(root)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "gludd-native-guard-fixture"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (root / "anchor.txt").write_text("base anchor\n", encoding="utf-8")
    _native_git(root, "add", "--", ".pre-commit-config.yaml", "pyproject.toml", "anchor.txt")
    _native_git(root, "-c", "core.hooksPath=/dev/null", "commit", "-m", "fixture base")
    trace = tmp_path / "native-precommit-argv.jsonl"
    adapter = tmp_path / "native-uv-adapter.py"
    real_uv = shutil.which("uv")
    assert real_uv is not None
    adapter.write_text(
        "import json, os, sys\n"
        f"with open({str(trace)!r}, 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'argv': sys.argv[1:], "
        "'index': os.environ.get('PIP_INDEX_URL'), "
        "'carrier': os.environ.get('GUARD_CARRIER'), "
        "'flags': os.environ.get('MAKEFLAGS', ''), "
        "'level': os.environ.get('MAKELEVEL', '')}) + '\\n')\n"
        f"os.execve({real_uv!r}, [{real_uv!r}, *sys.argv[1:]], dict(os.environ))\n",
        encoding="utf-8",
    )
    source = SHIPPING_PATH.read_text(encoding="utf-8")
    start = source.index("\n_pre-commit-secrets-guard:") + 1
    guard = source[start : source.index("\n\n", start)]
    (root / "Makefile").write_text("UV := false\n" + guard + "\n", encoding="utf-8")
    return root, adapter


def _native_guard(
    root: Path,
    adapter: Path,
    *,
    environment_override: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            *(("-e",) if environment_override else ()),
            "_pre-commit-secrets-guard",
            "UV=" + shlex.join((sys.executable, str(adapter))),
        ],
        cwd=root,
        env=_native_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )


def _native_calls(adapter: Path) -> list[dict[str, object]]:
    trace = adapter.parent / "native-precommit-argv.jsonl"
    if not trace.exists():
        return []

    def _normalize(argv: list[str]) -> list[str]:
        # The guard passes --active to uv so it uses the active environment; strip
        # it from the recorded argv so existing assertions remain stable.
        if (
            len(argv) >= 4
            and argv[0] == "run"
            and argv[1] == "--active"
            and argv[2] == "pre-commit"
            and argv[3] == "run"
        ):
            return ["run", "pre-commit", "run", *argv[4:]]
        return argv

    return [
        {**json.loads(line), "argv": _normalize(json.loads(line)["argv"])}
        for line in trace.read_text(encoding="utf-8").splitlines()
        if _normalize(json.loads(line)["argv"])[:3] == ["run", "pre-commit", "run"]
    ]


def _native_identity(root: Path) -> tuple[str, str, str, bytes]:
    return (
        _native_git(root, "rev-parse", "HEAD").stdout,
        _native_git(root, "write-tree").stdout,
        _native_git(root, "show-ref").stdout,
        (root / ".git/MERGE_HEAD").read_bytes() if (root / ".git/MERGE_HEAD").exists() else b"",
    )


def _native_merge(root: Path, bad_file: str, contents: str) -> list[str]:
    _native_git(root, "checkout", "-b", "incoming")
    names = [bad_file, "merged\nnewline.txt", "incoming-clean.txt"]
    for name in names:
        (root / name).write_text(contents if name == bad_file else "merged clean\n", encoding="utf-8")
    _native_git(root, "add", "--", *names)
    _native_git(root, "-c", "core.hooksPath=/dev/null", "commit", "-m", "clean incoming merge")
    _native_git(root, "checkout", "base")
    (root / "other-side.txt").write_text("independent branch content\n", encoding="utf-8")
    _native_git(root, "add", "--", "other-side.txt")
    _native_git(root, "-c", "core.hooksPath=/dev/null", "commit", "-m", "other branch")
    _native_git(root, "merge", "--no-ff", "--no-commit", "incoming")
    assert _native_git(root, "diff", "--name-only", "--diff-filter=U").stdout == ""
    assert (root / ".git/MERGE_HEAD").exists()
    return names


@pytest.mark.parametrize("environment_override", (False, True))
def test_native_clean_merge_guard_checks_all_staged_formatter_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment_override: bool,
) -> None:
    """A conflict-free merge still owes the actual native whitespace hook."""
    monkeypatch.setenv("GUARD_CARRIER", "opaque n q t carrier with  two spaces")
    monkeypatch.setenv("UV", "/usr/bin/false")
    monkeypatch.setenv("PIP_INDEX_URL", "https://private.invalid/simple")
    root, adapter = _native_workspace(tmp_path)
    bad_file = "clean merged whitespace.txt"
    names = _native_merge(root, bad_file, "owned merged content  \n")
    decoy = root / "unstaged-decoy.txt"
    decoy.write_text("outside staged scope  \n", encoding="utf-8")
    before = _native_identity(root)
    baseline = (ROOT / ".secrets.baseline").read_bytes()

    result = _native_guard(root, adapter, environment_override=environment_override)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "trailing-whitespace" in output and "Failed" in output, output
    assert (root / bad_file).read_text(encoding="utf-8") == "owned merged content\n"
    assert decoy.read_text(encoding="utf-8") == "outside staged scope  \n"
    assert _native_identity(root) == before
    calls = _native_calls(adapter)
    assert len(calls) == 1, calls
    arguments = cast(list[str], calls[0]["argv"])
    assert arguments[:5] == ["run", "pre-commit", "run", "trailing-whitespace", "--files"]
    forwarded = arguments[5:]
    assert {name.removeprefix("./") for name in forwarded} == set(names)
    assert all(call["index"] == "https://pypi.org/simple" for call in calls)
    assert all(call["carrier"] == "opaque n q t carrier with  two spaces" for call in calls)
    assert (ROOT / ".secrets.baseline").read_bytes() == baseline


@pytest.mark.parametrize("environment_override", (False, True))
def test_native_clean_merge_guard_checks_real_readonly_secret_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment_override: bool,
) -> None:
    monkeypatch.setenv("GUARD_CARRIER", "readonly secret carrier")
    root, adapter = _native_workspace(tmp_path)
    bad_file = "merged-secret.py"
    _native_merge(root, bad_file, f"password = {secrets.token_hex(32)!r}\n")
    before = _native_identity(root)
    working = (root / bad_file).read_bytes()
    baseline = (ROOT / ".secrets.baseline").read_bytes()

    result = _native_guard(root, adapter, environment_override=environment_override)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "Detect secrets" in output and "Failed" in output, output
    assert "Potential secrets" in output, output
    assert _native_identity(root) == before
    assert (root / bad_file).read_bytes() == working
    assert (ROOT / ".secrets.baseline").read_bytes() == baseline
    calls = _native_calls(adapter)
    assert [cast(list[str], call["argv"])[3] for call in calls] == list(NATIVE_HOOKS)
    assert all("--files" in cast(list[str], call["argv"]) for call in calls)


@pytest.mark.parametrize("filename", ("ordinary space.txt", "native\nnewline.txt", "--native-leading.txt"))
def test_native_precommit_explicit_filename_grammar(tmp_path: Path, filename: str) -> None:
    """Measure native argparse separately from the production guard."""
    root, _ = _native_workspace(tmp_path)
    path = root / filename
    path.write_text("owned native grammar  \n", encoding="utf-8")
    _native_git(root, "add", "--", filename)
    command = [sys.executable, "-m", "pre_commit", "run", "trailing-whitespace", "--files"]
    raw = subprocess.run(
        [*command, filename],
        cwd=root,
        env=_native_environment(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if filename.startswith("-"):
        assert raw.returncode == 2, raw.stdout + raw.stderr
        assert "unrecognized arguments" in raw.stderr
        assert path.read_text(encoding="utf-8") == "owned native grammar  \n"
        normalized = subprocess.run(
            [*command, "./" + filename],
            cwd=root,
            env=_native_environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert normalized.returncode == 1, normalized.stdout + normalized.stderr
        assert "trailing-whitespace-fixer: error: unrecognized arguments" in normalized.stdout
        assert path.read_text(encoding="utf-8") == "owned native grammar  \n"
    else:
        assert raw.returncode == 1, raw.stdout + raw.stderr
        assert path.read_text(encoding="utf-8") == "owned native grammar\n"


@pytest.mark.parametrize("environment_override", (False, True))
def test_native_clean_merge_guard_preserves_real_end_of_file_failure(
    tmp_path: Path,
    environment_override: bool,
) -> None:
    root, adapter = _native_workspace(tmp_path)
    filename = "merged missing newline.txt"
    _native_merge(root, filename, "owned missing newline")
    before = _native_identity(root)
    result = _native_guard(root, adapter, environment_override=environment_override)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "hook id: end-of-file-fixer" in output and "Failed" in output
    assert (root / filename).read_text(encoding="utf-8") == "owned missing newline\n"
    assert _native_identity(root) == before
    assert [cast(list[str], call["argv"])[3] for call in _native_calls(adapter)] == list(NATIVE_HOOKS[:2])


@pytest.mark.parametrize("environment_override", (False, True))
def test_native_clean_merge_guard_success_retains_complete_explicit_scope(
    tmp_path: Path,
    environment_override: bool,
) -> None:
    root, adapter = _native_workspace(tmp_path)
    names = _native_merge(root, "clean merged file.txt", "owned clean content\n")
    before = _native_identity(root)
    baseline = (ROOT / ".secrets.baseline").read_bytes()
    result = _native_guard(root, adapter, environment_override=environment_override)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.count("Passed") == 3, output
    assert "Checking merge-conflict files only" not in output
    assert "Skipped" not in output
    assert "outcome=passed" in output and "selection=native-hook-output" in output
    assert "gate_evidence=false" in output and "release_ready=false" in output
    calls = _native_calls(adapter)
    assert [cast(list[str], call["argv"])[3] for call in calls] == list(NATIVE_HOOKS)
    for call in calls:
        assert set(cast(list[str], call["argv"])[5:]) == set(names), call
    assert _native_identity(root) == before
    assert (ROOT / ".secrets.baseline").read_bytes() == baseline


@pytest.mark.parametrize("environment_override", (False, True))
def test_native_guard_empty_index_starts_no_hooks_or_security_claim(
    tmp_path: Path,
    environment_override: bool,
) -> None:
    root, adapter = _native_workspace(tmp_path)
    decoy = root / "untracked secret.txt"
    decoy.write_text("owned untracked trailing space  \n", encoding="utf-8")
    before = _native_identity(root)
    result = _native_guard(root, adapter, environment_override=environment_override)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert _native_calls(adapter) == []
    assert "outcome=empty" in output and "staged_files=0 hook_commands=0" in output
    assert "no-staged-scope-was-measured" in output and "outcome=passed" not in output
    assert _native_identity(root) == before
    assert decoy.read_text(encoding="utf-8") == "owned untracked trailing space  \n"


@pytest.mark.parametrize("environment_override", (False, True))
def test_native_guard_refuses_option_looking_scope_before_any_hook(
    tmp_path: Path,
    environment_override: bool,
) -> None:
    root, adapter = _native_workspace(tmp_path)
    filename = "--markdown-linebreak-ext=txt"
    (root / filename).write_text("owned unsupported filename  \n", encoding="utf-8")
    _native_git(root, "add", "--", filename)
    before = _native_identity(root)
    result = _native_guard(root, adapter, environment_override=environment_override)
    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "failed_input=option-looking-staged-path" in output
    assert _native_calls(adapter) == []
    assert _native_identity(root) == before
    assert (root / filename).read_text(encoding="utf-8") == "owned unsupported filename  \n"


@pytest.mark.parametrize("environment_override", (False, True))
@pytest.mark.parametrize(
    ("owner", "failure"),
    (
        ("staged", 73),
        ("staged", -15),
        ("staged", "unterminated"),
        ("unmerged", 73),
        ("unmerged", -15),
    ),
)
def test_native_inventory_failure_never_runs_a_hook_or_changes_owned_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment_override: bool,
    owner: str,
    failure: int | str,
) -> None:
    root, adapter = _native_workspace(tmp_path)
    _native_merge(root, "clean inventory.txt", "owned clean input\n")
    before = _native_identity(root)
    real_git = shutil.which("git")
    assert real_git is not None
    binary = tmp_path / "inventory-bin"
    binary.mkdir()
    inventory_calls = tmp_path / "inventory-argv.jsonl"
    proxy = binary / "git"
    expected_query = ["--no-optional-locks", "diff", "--cached", "--name-only"]
    if owner == "unmerged":
        expected_query.append("--diff-filter=U")
    expected_query.extend(("-z", "--"))
    proxy.write_text(
        f"#!{sys.executable}\n"
        "import json, os, signal, sys\n"
        "args = sys.argv[1:]\n"
        f"if args == {expected_query!r}:\n"
        f"    with open({str(inventory_calls)!r}, 'a') as stream:\n"
        "        stream.write(json.dumps(args) + '\\n')\n"
        f"    status = {failure!r}\n"
        "    if status == 'unterminated':\n"
        "        sys.stdout.buffer.write(b'incomplete staged filename')\n"
        "        raise SystemExit(0)\n"
        "    if status < 0:\n"
        "        os.kill(os.getpid(), -status)\n"
        "    print('owned native staged inventory refusal', file=sys.stderr)\n"
        "    raise SystemExit(status)\n"
        f"os.execve({real_git!r}, [{real_git!r}, *args], dict(os.environ))\n",
        encoding="utf-8",
    )
    proxy.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    result = _native_guard(root, adapter, environment_override=environment_override)
    output = result.stdout + result.stderr
    expected = 2 if failure == "unterminated" else 143 if failure == -15 else 73
    assert result.returncode == 2 and f"Error {expected}" in output, output
    assert len(inventory_calls.read_text(encoding="utf-8").splitlines()) == 1
    assert _native_calls(adapter) == []
    assert _native_identity(root) == before
    assert "gate_evidence=false" in output
    if failure == "unterminated":
        assert "failed_input=unterminated-staged-NUL-inventory" in output
    else:
        assert f"failed_owner=git-{owner}-paths" in output


@pytest.mark.parametrize("environment_override", (False, True))
@pytest.mark.parametrize("native_status", (73, -15))
def test_native_hook_status_and_signal_stop_before_later_hooks(
    tmp_path: Path,
    environment_override: bool,
    native_status: int,
) -> None:
    root, _ = _native_workspace(tmp_path)
    filename = "owned native status.txt"
    (root / filename).write_text("owned staged input\n", encoding="utf-8")
    _native_git(root, "add", "--", filename)
    (root / filename).write_text("owned unstaged input\n", encoding="utf-8")
    before = _native_identity(root)
    trace = tmp_path / "native-status.jsonl"
    stub = tmp_path / "native-status.py"
    stub.write_text(
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['run', '--active']:\n"
        "    args = ['run'] + args[2:]\n"
        "if args[:2] == ['run', 'python']:\n"
        "    os.execve(sys.executable, [sys.executable, *args[2:]], dict(os.environ))\n"
        f"with open({str(trace)!r}, 'a') as stream:\n"
        "    stream.write(json.dumps(args) + '\\n')\n"
        "if args[3] == 'end-of-file-fixer':\n"
        f"    status = {native_status}\n"
        "    if status < 0:\n"
        "        os.kill(os.getpid(), -status)\n"
        "    raise SystemExit(status)\n",
        encoding="utf-8",
    )
    result = _native_guard(root, stub, environment_override=environment_override)
    output = result.stdout + result.stderr
    expected = 143 if native_status == -15 else native_status
    assert result.returncode == 2 and f"Error {expected}" in output, output
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert [call[3] for call in calls] == list(NATIVE_HOOKS[:2])
    assert f"native_status={expected}" in output and "failed_hook=end-of-file-fixer" in output
    assert _native_identity(root) == before
    assert (root / filename).read_text(encoding="utf-8") == "owned unstaged input\n"


@pytest.mark.parametrize("environment_override", (False, True))
def test_native_deleted_only_scope_retains_native_skip_proof_limits(
    tmp_path: Path,
    environment_override: bool,
) -> None:
    root, adapter = _native_workspace(tmp_path)
    _native_git(root, "rm", "--", "anchor.txt")
    before = _native_identity(root)
    result = _native_guard(root, adapter, environment_override=environment_override)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    calls = _native_calls(adapter)
    assert len(calls) == 3
    assert all(cast(list[str], call["argv"])[5:] == ["anchor.txt"] for call in calls)
    assert output.count("Skipped") == 3, output
    assert "selection=native-hook-output" in output and "skipped-hooks-measure-no-matching-files" in output
    assert "gate_evidence=false" in output and "release_ready=false" in output
    assert _native_identity(root) == before


@pytest.mark.parametrize("environment_override", (False, True))
def test_native_unmerged_index_never_becomes_empty_success(
    tmp_path: Path,
    environment_override: bool,
) -> None:
    root, adapter = _native_workspace(tmp_path)
    _native_git(root, "checkout", "-b", "conflicted")
    (root / "anchor.txt").write_text("incoming anchor\n", encoding="utf-8")
    _native_git(root, "add", "--", "anchor.txt")
    _native_git(root, "-c", "core.hooksPath=/dev/null", "commit", "-m", "conflicting incoming")
    _native_git(root, "checkout", "base")
    (root / "anchor.txt").write_text("other anchor\n", encoding="utf-8")
    _native_git(root, "add", "--", "anchor.txt")
    _native_git(root, "-c", "core.hooksPath=/dev/null", "commit", "-m", "conflicting base")
    merge = subprocess.run(
        ["git", "merge", "--no-ff", "--no-commit", "conflicted"],
        cwd=root,
        env=_native_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert merge.returncode == 1, merge.stdout + merge.stderr
    before = (
        _native_git(root, "rev-parse", "HEAD").stdout,
        _native_git(root, "ls-files", "--stage", "-z").stdout,
        (root / "anchor.txt").read_bytes(),
        (root / ".git/MERGE_HEAD").read_bytes(),
    )
    implicit = subprocess.run(
        [sys.executable, "-m", "pre_commit", "run", "trailing-whitespace"],
        cwd=root,
        env=_native_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert implicit.returncode != 0 and "unmerged" in (implicit.stdout + implicit.stderr).lower()
    result = _native_guard(root, adapter, environment_override=environment_override)
    output = result.stdout + result.stderr
    assert result.returncode != 0 and "unmerged" in output.lower(), output
    assert _native_calls(adapter) == []
    assert "outcome=empty" not in output and "outcome=passed" not in output
    assert (
        _native_git(root, "rev-parse", "HEAD").stdout,
        _native_git(root, "ls-files", "--stage", "-z").stdout,
        (root / "anchor.txt").read_bytes(),
        (root / ".git/MERGE_HEAD").read_bytes(),
    ) == before


STAGED_CONTEXT_PROBE = r"""import inspect
import subprocess
import sys
from pre_commit.staged_files_only import staged_files_only
from pre_commit.store import Store

store = Store()
assert isinstance(store.directory, str)
context = staged_files_only(store.directory)
print("NATIVE_PRECOMMIT_API staged_files_only=" + str(inspect.signature(staged_files_only))
      + " store_directory_type=" + type(store.directory).__name__
      + " context_type=" + type(context).__name__, flush=True)
print("NATIVE_PRECOMMIT_API doc=" + str(staged_files_only.__doc__ or "")[:160], flush=True)
with context:
    for hook in ("trailing-whitespace", "end-of-file-fixer", "detect-secrets-readonly"):
        result = subprocess.run(
            [*sys.argv[2:], "run", "pre-commit", "run", hook, "--files", sys.argv[1]], check=False,
        )
        if result.returncode:
            break
raise SystemExit(result.returncode if result.returncode >= 0 else 128 - result.returncode)
"""


def _exercise_native_staged_byte_boundary(
    tmp_path: Path,
    environment_override: bool,
    staged_bad: bool,
    kind: str,
    *,
    use_context: bool,
) -> None:
    root, adapter = _native_workspace(tmp_path)
    filename = "split staged bytes.txt" if kind == "formatter" else "split-staged-secret.py"
    clean = "owned clean content\n" if kind == "formatter" else "message = 'owned clean content'\n"
    bad = "owned clean content  \n" if kind == "formatter" else f"password = {secrets.token_hex(32)!r}\n"
    staged, working = (bad, clean) if staged_bad else (clean, bad)
    (root / filename).write_text(staged, encoding="utf-8")
    _native_git(root, "add", "--", filename)
    (root / filename).write_text(working, encoding="utf-8")
    before = _native_identity(root)
    baseline = (ROOT / ".secrets.baseline").read_bytes()
    hook = "trailing-whitespace" if kind == "formatter" else "detect-secrets-readonly"

    implicit = subprocess.run(
        [sys.executable, "-m", "pre_commit", "run", hook],
        cwd=root,
        env=_native_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    implicit_output = implicit.stdout + implicit.stderr
    assert (implicit.returncode != 0) is staged_bad, implicit_output
    assert _native_git(root, "show", f":{filename}").stdout == staged
    assert (root / filename).read_text(encoding="utf-8") == working, implicit_output
    assert _native_identity(root) == before

    if use_context:
        environment = _native_environment()
        environment["PIP_INDEX_URL"] = "https://pypi.org/simple"
        result = subprocess.run(
            [sys.executable, "-c", STAGED_CONTEXT_PROBE, filename, sys.executable, str(adapter)],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
        )
        print(result.stdout)
    else:
        result = _native_guard(root, adapter, environment_override=environment_override)

    output = result.stdout + result.stderr
    assert (result.returncode != 0) is staged_bad, output
    assert _native_git(root, "show", f":{filename}").stdout == staged
    assert (root / filename).read_text(encoding="utf-8") == working, output
    assert _native_identity(root) == before
    assert (ROOT / ".secrets.baseline").read_bytes() == baseline


@pytest.mark.parametrize("environment_override", (False, True))
@pytest.mark.parametrize("staged_bad", (False, True))
@pytest.mark.parametrize("kind", ("formatter", "secret"))
def test_native_guard_measures_staged_bytes_and_preserves_unstaged_changes(
    tmp_path: Path,
    environment_override: bool,
    staged_bad: bool,
    kind: str,
) -> None:
    """Cached filenames alone cannot certify different working-tree bytes."""
    _exercise_native_staged_byte_boundary(
        tmp_path,
        environment_override,
        staged_bad,
        kind,
        use_context=False,
    )


@pytest.mark.parametrize("staged_bad", (False, True))
@pytest.mark.parametrize("kind", ("formatter", "secret"))
def test_pinned_precommit_staged_context_preserves_index_boundary(
    tmp_path: Path,
    staged_bad: bool,
    kind: str,
) -> None:
    """Measure the installed mature context before making it production policy."""
    _exercise_native_staged_byte_boundary(
        tmp_path,
        False,
        staged_bad,
        kind,
        use_context=True,
    )


@pytest.mark.parametrize("environment_override", (False, True))
@pytest.mark.parametrize("module_name", ("pre_commit", "subprocess"))
def test_native_guard_does_not_import_untracked_implicit_cwd_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment_override: bool,
    module_name: str,
) -> None:
    """Keep the installed entrypoint's boundary while retaining explicit carriers."""
    root, adapter = _native_workspace(tmp_path)
    filename = "owned staged shadow control.txt"
    (root / filename).write_text("owned clean content\n", encoding="utf-8")
    _native_git(root, "add", "--", filename)
    before = _native_identity(root)
    carrier = tmp_path / "explicit python carrier"
    carrier.mkdir()
    startup_trace = tmp_path / "startup-carriers.jsonl"
    (carrier / "sitecustomize.py").write_text(
        "import json, os, sys\n"
        f"with open({str(startup_trace)!r}, 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'argv': sys.argv, 'cwd': os.getcwd(), "
        "'pythonpath': os.environ.get('PYTHONPATH'), "
        "'carrier': os.environ.get('GUARD_CARRIER'), "
        "'safe_path': sys.flags.safe_path}) + '\\n')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(carrier))
    monkeypatch.setenv("GUARD_CARRIER", "explicit carrier  n q t must remain")
    # This fixture owns the implicit-CWD input; a truthy inherited safe-path
    # switch would make its unguarded activation witness inert.
    if os.environ.get("PYTHONSAFEPATH"):
        monkeypatch.delenv("PYTHONSAFEPATH")
    environment = _native_environment()
    environment["PIP_INDEX_URL"] = "https://pypi.org/simple"
    entered = tmp_path / f"{module_name}-executed"
    shadow = root / f"{module_name}.py"
    shadow.write_text(
        "from pathlib import Path\n"
        f"Path({str(entered)!r}).write_text('owned implicit CWD module executed')\n"
        "raise SystemExit(79)\n",
        encoding="utf-8",
    )
    command = [sys.executable, str(adapter), "run"]
    original = subprocess.run(
        [*command, "pre-commit", "run", "trailing-whitespace", "--files", filename],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert original.returncode == 0, original.stdout + original.stderr
    assert not entered.exists(), "the original installed entrypoint was not isolated"

    unguarded = subprocess.run(
        [*command, "python", "-c", f"import {module_name}"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert unguarded.returncode == 79, unguarded.stdout + unguarded.stderr
    assert entered.read_text(encoding="utf-8") == "owned implicit CWD module executed"
    entered.unlink()  # Remove only the owned witness; retain identical module bytes/context.
    previous_events = len(startup_trace.read_text(encoding="utf-8").splitlines())
    inherited = os.environ.copy()

    result = _native_guard(root, adapter, environment_override=environment_override)

    output = result.stdout + result.stderr
    assert not entered.exists(), output
    assert result.returncode == 0, output
    assert _native_identity(root) == before
    assert (root / filename).read_text(encoding="utf-8") == "owned clean content\n"
    assert os.environ == inherited
    events = [json.loads(line) for line in startup_trace.read_text(encoding="utf-8").splitlines()[previous_events:]]
    bootstrap = [event for event in events if event["argv"] == ["-c", sys.executable, str(adapter)]]
    assert bootstrap, events
    assert all(event["pythonpath"] == str(carrier) for event in bootstrap)
    assert all(event["carrier"] == "explicit carrier  n q t must remain" for event in bootstrap)
    assert all(event["safe_path"] is True for event in bootstrap)
