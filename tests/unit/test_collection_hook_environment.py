"""Real local pre-push hooks keep Git-local state outside full collection."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.unit.test_single_native_commit_collection import _source

ROOT = Path(__file__).resolve().parents[2]
MAKE_KEYS = {"MAKEFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "MFLAGS", "GNUMAKEFLAGS"}
PRESERVED_KEYS = (
    "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "UV_NO_SYNC", "UV_PYTHON_DOWNLOADS",
    "NPROC", "GLUDD_XDIST_WORKERS", "GIT_EDITOR", "GIT_OPTIONAL_LOCKS", "COLLECTION_LITERAL",
)


@dataclass(frozen=True)
class HookFixture:
    main: Path
    linked: Path
    remote: Path
    native_git: str
    environment: dict[str, str] = field(repr=False)
    hook_snapshot: Path
    collection_snapshot: Path

    def git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.native_git, *args], cwd=cwd or self.linked, env=self.environment,
            capture_output=True, text=True, check=False, timeout=30,
        )


def _create_local_hook(tmp_path: Path) -> HookFixture:
    native_git = shutil.which("git")
    assert native_git is not None
    environment = {key: value for key, value in os.environ.items() if key not in MAKE_KEYS}
    environment.pop("PYTEST_ADDOPTS", None)
    inventory = subprocess.run(
        [native_git, "rev-parse", "--local-env-vars"], cwd=tmp_path,
        env={"PATH": os.defpath}, capture_output=True, text=True, check=True, timeout=5,
    ).stdout
    names = inventory.split()
    assert 0 < len(names) <= 64 and len(inventory) <= 8192
    assert all(name.startswith("GIT_") and name.isascii() and name.isidentifier() for name in names)
    for name in names:
        environment.pop(name, None)
    main, linked, remote = (tmp_path / name for name in ("main", "linked", "remote.git"))
    main.mkdir()
    fixture = HookFixture(
        main, linked, remote, native_git, environment,
        tmp_path / "hook.json", tmp_path / "collection.json",
    )
    (main / "preserved.txt").write_text("tracked source remains unchanged\n", encoding="utf-8")
    for args, cwd in (
        (("init", "-q", "--initial-branch=main"), main),
        (("config", "user.email", "collection-fixture@example.invalid"), main),
        (("config", "user.name", "Collection fixture"), main),
        (("add", "preserved.txt"), main),
        (("-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", "fixture"), main),
        (("worktree", "add", "-q", "-b", "hook-fixture", str(linked)), main),
        (("init", "-q", "--bare", str(remote)), main),
    ):
        result = fixture.git(*args, cwd=cwd)
        assert result.returncode == 0, result.stderr
    (linked / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    tests = linked / "tests"
    tests.mkdir()
    (tests / "test_inventory.py").write_text(
        "import pytest\n@pytest.mark.parametrize('number', range(3))\n"
        "def test_fixture(number):\n    raise AssertionError('collection executed a test')\n",
        encoding="utf-8",
    )
    (linked / "pytest.ini").write_text("[pytest]\nfilterwarnings = error\n", encoding="utf-8")
    (linked / "conftest.py").write_text(
        "import json, os, subprocess, sys\nfrom pathlib import Path\n"
        "from tests.conftest import _WorktreeConfinement\n"
        f"MAIN = Path({str(main)!r})\nLINKED = Path({str(linked)!r})\n"
        "def pytest_configure(config):\n"
        "    env = dict(os.environ)\n"
        f"    kept = {{key: env.get(key) for key in {PRESERVED_KEYS!r}}}\n"
        "    local = subprocess.run(['git', 'rev-parse', '--local-env-vars'], check=True,\n"
        "                           capture_output=True, text=True).stdout.split()\n"
        "    leaks = sorted(key for key, value in env.items() if str(MAIN) in value)\n"
        "    Path(env['COLLECTION_SNAPSHOT']).write_text(json.dumps({\n"
        "        'local_present': sorted(set(local) & env.keys()), 'canonical_keys': leaks,\n"
        "        'preserved': kept, 'cwd': str(Path.cwd()), 'ansible_tmp': env.get('ANSIBLE_LOCAL_TEMP'),\n"
        "    }), encoding='utf-8')\n"
        "    args = [sys.executable, '-c', 'pass']\n"
        "    _WorktreeConfinement(active_root=LINKED, canonical_root=MAIN).audit(\n"
        "        'subprocess.Popen', (sys.executable, args, str(LINKED), env))\n"
        "    subprocess.run(args, env=env, check=True)\n",
        encoding="utf-8",
    )
    uv = tmp_path / "native_uv.py"
    uv.write_text(
        "import os, sys\nargs = sys.argv[1:]\nassert args.pop(0) == 'run'\n"
        "if args[:1] == ['--no-sync']: args.pop(0)\n"
        "assert args.pop(0) == 'python'\nos.execv(sys.executable, [sys.executable, *args])\n",
        encoding="utf-8",
    )
    (linked / "Makefile").write_text(
        f"UV := {shlex.quote(sys.executable)} {shlex.quote(str(uv))}\n" + _source(), encoding="utf-8",
    )
    hook = main / ".git" / "hooks" / "pre-push"
    hook.write_text(
        f"#!{sys.executable}\nimport json, os, subprocess\nfrom pathlib import Path\n"
        "before = dict(os.environ)\n"
        f"local = subprocess.run([{native_git!r}, 'rev-parse', '--local-env-vars'], check=True,\n"
        "                       capture_output=True, text=True).stdout.split()\n"
        "command = ['make', '--no-print-directory', os.environ['COLLECTION_TARGET'],\n"
        "    'OBSERVED_ROOT=' + os.environ['COLLECTION_OBSERVED_ROOT'],\n"
        "    'OBSERVED_HEARTBEAT_SECS=1', 'OBSERVED_QUIET_SECS=30', 'OBSERVED_MAX_SECS=30',\n"
        "    'OBSERVED_RETAIN_RUNS=2', 'OBSERVED_TAIL_LINES=80']\n"
        "if os.environ.get('COLLECTION_GIT_MAKE_OVERRIDE') == '1':\n"
        "    command.append('GIT_DIR=' + before['GIT_DIR'])\n"
        "child = dict(before)\n"
        "if before.get('COLLECTION_GIT_DISCOVERY_PATH'):\n"
        "    child['PATH'] = before['COLLECTION_GIT_DISCOVERY_PATH'] + os.pathsep + before['PATH']\n"
        "result = subprocess.run(command, env=child)\n"
        f"canonical = {str(main)!r}\n"
        "Path(os.environ['HOOK_SNAPSHOT']).write_text(json.dumps({\n"
        "    'canonical_keys': sorted(key for key, value in before.items() if canonical in value),\n"
        "    'local_present': sorted(set(local) & before.keys()),\n"
        "    'parent_unchanged': before == dict(os.environ), 'cwd': str(Path.cwd()),\n"
        "    'rc': result.returncode,\n"
        "}), encoding='utf-8')\nraise SystemExit(result.returncode)\n",
        encoding="utf-8",
    )
    hook.chmod(0o700)
    environment.update({
        "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "GLUDD_PROJECT_ROOT": str(linked), "COLLECTION_TARGET": "collect-check",
        "HOOK_SNAPSHOT": str(fixture.hook_snapshot), "COLLECTION_SNAPSHOT": str(fixture.collection_snapshot),
        "COLLECTION_OBSERVED_ROOT": str(tmp_path / "observed"), "GIT_EDITOR": "collection-editor",
        "GIT_OPTIONAL_LOCKS": "0", "COLLECTION_LITERAL": "quoted spaces; $literal 'value'",
    })
    return fixture


@pytest.fixture
def local_hook(tmp_path: Path) -> HookFixture:
    return _create_local_hook(tmp_path)


def _git_authority_bytes(repository: Path) -> dict[str, bytes]:
    metadata = repository / ".git"
    result = {name: (metadata / name).read_bytes() for name in ("HEAD", "index", "config")}
    result.update({
        str(path.relative_to(metadata)): path.read_bytes()
        for path in (metadata / "refs").rglob("*") if path.is_file()
    })
    return result


@pytest.mark.parametrize("variable", ["GIT_DIR", "GIT_INDEX_FILE"])
def test_fixture_setup_never_routes_mutations_into_ambient_owned_repository(
    local_hook: HookFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str,
) -> None:
    sentinel = local_hook.main
    before = _git_authority_bytes(sentinel)
    value = sentinel / ".git" / "index" if variable == "GIT_INDEX_FILE" else sentinel / ".git"
    monkeypatch.setenv(variable, str(value))
    environment_before = dict(os.environ)
    isolated = tmp_path / "isolated-setup"
    isolated.mkdir()
    created: HookFixture | None = None
    error: Exception | None = None
    try:
        created = _create_local_hook(isolated)
    except Exception as exc:
        error = exc
    assert _git_authority_bytes(sentinel) == before, f"ambient {variable} redirected fixture setup mutations"
    assert os.environ == environment_before, "setup isolation must not alter the caller environment"
    assert error is None, f"fixture setup failed: {error}"
    assert created is not None and variable not in created.environment
    for checkout in (created.main, created.linked):
        common = created.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=checkout)
        top = created.git("rev-parse", "--show-toplevel", cwd=checkout)
        assert common.returncode == top.returncode == 0
        assert Path(common.stdout.strip()) == created.main / ".git"
        assert Path(top.stdout.strip()) == checkout


@pytest.mark.parametrize("target", ["collect-check", "test-count"])
def test_real_pre_push_collection_isolates_only_native_git_local_environment(
    local_hook: HookFixture, target: str,
) -> None:
    local_hook.environment["COLLECTION_TARGET"] = target
    index = local_hook.main / ".git" / "index"
    before_index = hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None
    owner_environment = dict(os.environ)
    result = local_hook.git("push", str(local_hook.remote), "HEAD:refs/heads/fixture")
    hook = json.loads(local_hook.hook_snapshot.read_text(encoding="utf-8"))
    collected = json.loads(local_hook.collection_snapshot.read_text(encoding="utf-8"))
    assert hook["canonical_keys"] == ["GIT_DIR"], "real hook diagnosis contains key names only"
    assert hook["parent_unchanged"] is True
    assert hook["cwd"] == str(local_hook.linked)
    assert collected["canonical_keys"] == [], f"hook-local leaked keys: {collected['canonical_keys']}"
    assert collected["local_present"] == []
    assert result.returncode == 0, result.stdout + result.stderr
    assert collected["cwd"] == str(local_hook.linked)
    assert collected["preserved"] == {key: local_hook.environment.get(key) for key in PRESERVED_KEYS}
    assert os.environ == owner_environment
    assert (hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None) == before_index
    output = result.stdout + result.stderr
    expected_lock = local_hook.main / ".git" / "gludd" / "locks" / "collection.lock"
    logs = list((Path(local_hook.environment["COLLECTION_OBSERVED_ROOT"]) / target).glob("*.log"))
    assert len(logs) == 1
    observed = logs[0].read_text(encoding="utf-8")
    assert f"collection lock acquired: resource=collection path={expected_lock}" in observed
    assert "collection lock released:" in observed
    assert "Collection OK" in output
    assert "3 tests collected" in observed
    assert not Path(collected["ansible_tmp"]).exists()


def test_hook_regression_is_selected_by_existing_mandatory_runtime_owner(tmp_path: Path) -> None:
    from tests.unit.test_runtime_admission_scheduling import APPROVED_PROMOTION_SELECTORS, _capture_owner

    selector = (
        "tests/unit/test_collection_hook_environment.py::"
        "test_real_pre_push_collection_isolates_only_native_git_local_environment"
    )
    assert selector in APPROVED_PROMOTION_SELECTORS
    result, rows = _capture_owner(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    selected = rows[0]["files"]
    assert isinstance(selected, list) and selected.count(selector) == 1
    manifest = json.loads((ROOT / "config/gate_failure_promotions.json").read_text(encoding="utf-8"))
    assert {
        "id": "prepush-collection-git-local-environment",
        "owner_target": "_pytest-runtime-compatibility-admission",
        "node_kind": "pytest_node", "node": selector, "full_gate_phase": "test",
    } in manifest["families"]
    phase = next(row for row in manifest["runtime_contract"]["phases"]
                 if row["target"] == "_pytest-runtime-compatibility-admission")
    assert (phase["max_seconds"], phase["quiet_seconds"]) == (90, 60)


@pytest.mark.parametrize(("payload", "exit_code"), [
    ("GIT_DIR", 7), ("", 0), (" \n", 0), ("HOME", 0), ("GIT_DIR;false", 0),
    ("GIT_*", 0), ("GIT_DIR " * 65, 0), ("GIT_" + "A" * 8192, 0),
], ids=("git-failure", "empty", "whitespace", "non-git-name", "shell-data", "glob", "count", "bytes"))
def test_discovery_failure_or_invalid_native_name_inventory_starts_zero_collection_children(
    local_hook: HookFixture, payload: str, exit_code: int,
) -> None:
    binary = local_hook.linked.parent / "bin"
    binary.mkdir()
    git = binary / "git"
    git.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "if sys.argv[1:] == ['rev-parse', '--local-env-vars']:\n"
        f"    print({payload!r})\n    raise SystemExit({exit_code})\n"
        f"os.execv({local_hook.native_git!r}, [{local_hook.native_git!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    git.chmod(0o700)
    local_hook.environment["COLLECTION_GIT_DISCOVERY_PATH"] = str(binary)
    result = local_hook.git("push", str(local_hook.remote), "HEAD:refs/heads/fixture")
    hook = json.loads(local_hook.hook_snapshot.read_text(encoding="utf-8"))
    assert result.returncode != 0 and hook["parent_unchanged"] is True
    assert not local_hook.collection_snapshot.exists()
    assert not Path(local_hook.environment["COLLECTION_OBSERVED_ROOT"]).exists()
    assert "Collection OK" not in result.stdout
    if exit_code:
        assert f"Error {exit_code}" in result.stderr
    else:
        assert "Collection Git-local environment" in result.stderr


def test_temporary_parent_index_is_absent_only_in_collection_and_keeps_exact_bytes(local_hook: HookFixture) -> None:
    native_index = local_hook.git("rev-parse", "--git-path", "index")
    assert native_index.returncode == 0
    parent_index = local_hook.main / ".git" / "temporary-index"
    source = Path(native_index.stdout.strip())
    parent_index.write_bytes((source if source.is_absolute() else local_hook.linked / source).read_bytes())
    before = parent_index.read_bytes()
    local_hook.environment["GIT_INDEX_FILE"] = str(parent_index)
    head = local_hook.git("rev-parse", "HEAD").stdout
    result = local_hook.git("push", str(local_hook.remote), "HEAD:refs/heads/fixture")
    hook = json.loads(local_hook.hook_snapshot.read_text(encoding="utf-8"))
    collected = json.loads(local_hook.collection_snapshot.read_text(encoding="utf-8"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert hook["canonical_keys"] == ["GIT_DIR", "GIT_INDEX_FILE"]
    assert hook["parent_unchanged"] is True
    assert collected["canonical_keys"] == [] and collected["local_present"] == []
    assert parent_index.read_bytes() == before
    assert local_hook.environment["GIT_INDEX_FILE"] == str(parent_index)
    assert local_hook.git("rev-parse", "HEAD").stdout == head
    assert (local_hook.linked / "preserved.txt").read_text(encoding="utf-8") == "tracked source remains unchanged\n"


@pytest.mark.parametrize("transport", ["config-payload", "make-override"])
def test_unrelated_canonical_path_payload_still_fails_existing_confinement(
    local_hook: HookFixture, transport: str,
) -> None:
    if transport == "config-payload":
        local_hook.environment.update({
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": str(local_hook.main),
        })
        expected = "GIT_CONFIG_VALUE_0"
    else:
        local_hook.environment["COLLECTION_GIT_MAKE_OVERRIDE"] = "1"
        expected = "MAKEFLAGS"
    result = local_hook.git("push", str(local_hook.remote), "HEAD:refs/heads/fixture")
    hook = json.loads(local_hook.hook_snapshot.read_text(encoding="utf-8"))
    collected = json.loads(local_hook.collection_snapshot.read_text(encoding="utf-8"))
    assert result.returncode != 0 and hook["parent_unchanged"] is True
    assert expected in collected["canonical_keys"]
    assert collected["local_present"] == [], "native-listed keys are isolated even in unsupported context"
    output = result.stdout + result.stderr
    assert "pytest worktree confinement denied subprocess environment" in output
    assert "COLLECTION ERRORS DETECTED (rc=3" in output and "Collection OK" not in output


@pytest.mark.parametrize("failure", ["import", "warning"])
def test_hook_collection_retains_native_import_and_fatal_warning_failures(
    local_hook: HookFixture, failure: str,
) -> None:
    body = (
        "raise RuntimeError('native-hook-import-refusal')\n" if failure == "import"
        else "import warnings\nwarnings.warn('native-hook-warning-refusal', UserWarning)\n"
    )
    (local_hook.linked / "tests" / "test_refusal.py").write_text(body, encoding="utf-8")
    result = local_hook.git("push", str(local_hook.remote), "HEAD:refs/heads/fixture")
    hook = json.loads(local_hook.hook_snapshot.read_text(encoding="utf-8"))
    assert result.returncode != 0 and hook["parent_unchanged"] is True
    output = result.stdout + result.stderr
    assert "COLLECTION ERRORS DETECTED (rc=2" in output and "Collection OK" not in output
    assert f"native-hook-{failure}-refusal" in output


def test_direct_collection_repeats_fresh_inventory_under_same_canonical_lease(local_hook: HookFixture) -> None:
    for _ in range(2):
        result = subprocess.run([
            "make", "--no-print-directory", "test-count",
            "OBSERVED_ROOT=" + local_hook.environment["COLLECTION_OBSERVED_ROOT"],
            "OBSERVED_HEARTBEAT_SECS=1", "OBSERVED_QUIET_SECS=30", "OBSERVED_MAX_SECS=30",
            "OBSERVED_RETAIN_RUNS=2", "OBSERVED_TAIL_LINES=80",
        ], cwd=local_hook.linked, env=local_hook.environment, check=False, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "3 tests collected" in result.stdout and "Collection OK" in result.stdout
    logs = list((Path(local_hook.environment["COLLECTION_OBSERVED_ROOT"]) / "test-count").glob("*.log"))
    assert len(logs) == 2, "no previous collection or receipt can admit a later invocation"
    expected = local_hook.main / ".git" / "gludd" / "locks" / "collection.lock"
    assert all(f"collection lock acquired: resource=collection path={expected}" in log.read_text() for log in logs)


def test_native_hook_control_detects_mutated_missing_unset(local_hook: HookFixture) -> None:
    makefile = local_hook.linked / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    original = "unset $$GIT_LOCAL_ENV_NAMES || exit $$?; set +f;"
    assert text.count(original) == 1
    makefile.write_text(text.replace(original, "set +f;"), encoding="utf-8")
    result = local_hook.git("push", str(local_hook.remote), "HEAD:refs/heads/fixture")
    collected = json.loads(local_hook.collection_snapshot.read_text(encoding="utf-8"))
    assert result.returncode != 0 and collected["canonical_keys"] == ["GIT_DIR"]
    assert "pytest worktree confinement denied subprocess environment" in result.stdout + result.stderr




@pytest.mark.parametrize("collection_carrier_e", [False, True])
def test_pre_push_receives_explicit_ledger_makeflags_carrier(
    tmp_path: Path, collection_carrier_e: bool,
) -> None:
    """The native hook receives the actual owned duplicate, before confinement."""
    fixture = _create_local_hook(tmp_path)
    ledger = fixture.main / ".gludd" / "ci-failure-ledger.json"
    transport = tmp_path / "carrier-transport.json"
    recorder = tmp_path / "record-carrier-transport.py"
    recorder.write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        f"canonical = {str(fixture.main)!r}\nexpected = {str(ledger)!r}\n"
        "keys = sorted(key for key, value in os.environ.items() if canonical in value)\n"
        f"Path({str(transport)!r}).write_text(json.dumps({{\n"
        "    'keys': keys, 'MAKEFLAGS_origin': sys.argv[1],\n"
        "    'CI_FAILURE_LEDGER_origin': sys.argv[2],\n"
        "    'same_ledger': os.environ.get('CI_FAILURE_LEDGER') == expected,\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    makefile = fixture.linked / "Makefile"
    source = makefile.read_text(encoding="utf-8")
    uv_line = source.splitlines()[0]
    assert uv_line.startswith("UV := ")
    source += (
        "\n.PHONY: owned-ledger-publication\nowned-ledger-publication:\n\t@"
        + shlex.quote(sys.executable) + " " + shlex.quote(str(recorder))
        + ' "$(origin MAKEFLAGS)" "$(origin CI_FAILURE_LEDGER)"\n\t@'
        + shlex.quote(fixture.native_git) + " push "
        + shlex.quote(str(fixture.remote)) + " HEAD:refs/heads/ledger-probe\n"
    )
    makefile.write_text(source, encoding="utf-8")
    environment = dict(fixture.environment)
    environment["MAKEFLAGS"] = "-- CI_FAILURE_LEDGER=" + str(ledger)
    result = subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if collection_carrier_e else []),
         "owned-ledger-publication", "CI_FAILURE_LEDGER=" + str(ledger),
         "UV=" + uv_line.removeprefix("UV := ")],
        cwd=fixture.linked, env=environment,
        check=False, capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr
    assert transport.is_file(), output
    received = json.loads(transport.read_text(encoding="utf-8"))
    hook = json.loads(fixture.hook_snapshot.read_text(encoding="utf-8"))
    collected = json.loads(fixture.collection_snapshot.read_text(encoding="utf-8"))
    print("OWNED_LEDGER_NATIVE_TRANSPORT " + json.dumps({
        "early_environment": collection_carrier_e, **received,
        "hook_keys": hook["canonical_keys"], "collection_keys": collected["canonical_keys"],
    }, sort_keys=True))
    assert received["same_ledger"] is True
    assert received["CI_FAILURE_LEDGER_origin"] == "command line"
    assert received["MAKEFLAGS_origin"] not in ("undefined", "default")
    assert "MAKEFLAGS" in received["keys"] and "CI_FAILURE_LEDGER" in received["keys"]
    assert "MAKEFLAGS" in hook["canonical_keys"] and "CI_FAILURE_LEDGER" in hook["canonical_keys"]
    assert "MAKEFLAGS" in collected["canonical_keys"] and "CI_FAILURE_LEDGER" in collected["canonical_keys"]
    assert hook["parent_unchanged"] is True
    assert collected["preserved"] == {key: fixture.environment.get(key) for key in PRESERVED_KEYS}
    assert result.returncode != 0 and "pytest worktree confinement denied subprocess environment" in output


@pytest.mark.parametrize("collection_ledger_location", ["canonical", "active"])
@pytest.mark.parametrize("collection_ledger_e", [False, True])
def test_pre_push_collection_retains_ledger_path_confinement_with_real_ansible(
    tmp_path: Path, collection_ledger_location: str, collection_ledger_e: bool,
) -> None:
    """Native Make carries a ledger CLI value through the real pre-push hook."""
    fixture = _create_local_hook(tmp_path)
    # This fixture-owned legacy opt-out must not mask the real hook's plugins.
    fixture.environment.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
    conftest = fixture.linked / "conftest.py"
    source = conftest.read_text(encoding="utf-8")
    manual = (
        "    args = [sys.executable, '-c', 'pass']\n"
        "    _WorktreeConfinement(active_root=LINKED, canonical_root=MAIN).audit(\n"
        "        'subprocess.Popen', (sys.executable, args, str(LINKED), env))\n"
        "    subprocess.run(args, env=env, check=True)\n"
    )
    assert source.count(manual) == 1
    source = source.replace(
        manual, "    sys.addaudithook(_WorktreeConfinement(\n"
        "        active_root=LINKED, canonical_root=MAIN).audit)\n",
    )
    snapshot = "        'local_present': sorted(set(local) & env.keys()), 'canonical_keys': leaks,\n"
    assert source.count(snapshot) == 1
    source = source.replace(
        snapshot, snapshot
        + "        'ansible_plugin': any(getattr(plugin, '__name__', None) == "
        "'pytest_ansible.plugin' for plugin in config.pluginmanager.get_plugins()),\n",
    )
    conftest.write_text(source, encoding="utf-8")
    makefile = fixture.linked / "Makefile"
    source = makefile.read_text(encoding="utf-8")
    uv_line = source.splitlines()[0]
    assert uv_line.startswith("UV := ")
    source += (
        "\n.PHONY: owned-ledger-publication\nowned-ledger-publication:\n\t@"
        + shlex.quote(fixture.native_git) + " push "
        + shlex.quote(str(fixture.remote)) + " HEAD:refs/heads/ledger-probe\n"
    )
    makefile.write_text(source, encoding="utf-8")
    ledger_root = fixture.main if collection_ledger_location == "canonical" else fixture.linked
    ledger = ledger_root / ".gludd" / "ci-failure-ledger.json"
    environment = dict(fixture.environment)
    environment["MAKEFLAGS"] = "-- CI_FAILURE_LEDGER=" + str(ledger)
    environment_before = dict(os.environ)
    authority_before = _git_authority_bytes(fixture.main)
    result = subprocess.run(
        ["make", "--no-print-directory", *(["-e"] if collection_ledger_e else []),
         "owned-ledger-publication", "CI_FAILURE_LEDGER=" + str(ledger),
         "UV=" + uv_line.removeprefix("UV := ")],
        cwd=fixture.linked, env=environment,
        check=False, capture_output=True, text=True, timeout=30,
    )
    hook = json.loads(fixture.hook_snapshot.read_text(encoding="utf-8"))
    collected = json.loads(fixture.collection_snapshot.read_text(encoding="utf-8"))
    output = result.stdout + result.stderr
    print("COLLECTION_LEDGER_BOUNDARY " + json.dumps({
        "location": collection_ledger_location, "early_environment": collection_ledger_e,
        "hook_keys": hook["canonical_keys"], "collection_keys": collected["canonical_keys"],
        "native_status": result.returncode, "ansible_plugin": collected["ansible_plugin"],
        "ansible_failure_frame": "pytest_ansible/plugin.py" in output,
    }, sort_keys=True))
    assert hook["parent_unchanged"] is True
    assert hook["cwd"] == collected["cwd"] == str(fixture.linked)
    assert collected["local_present"] == []
    assert collected["preserved"] == {key: fixture.environment.get(key) for key in PRESERVED_KEYS}
    assert collected["ansible_plugin"] is True
    assert _git_authority_bytes(fixture.main) == authority_before
    assert os.environ == environment_before
    assert (fixture.linked / "preserved.txt").read_text(encoding="utf-8") == "tracked source remains unchanged\n"
    if collection_ledger_location == "canonical":
        assert "CI_FAILURE_LEDGER" in hook["canonical_keys"]
        assert "CI_FAILURE_LEDGER" in collected["canonical_keys"]
        assert "MAKEFLAGS" in collected["canonical_keys"]
        assert result.returncode != 0
        assert "pytest_ansible/plugin.py" in output
        assert "pytest worktree confinement denied subprocess environment" in output
        assert "COLLECTION ERRORS DETECTED (rc=3" in output and "Collection OK" not in output
        remote = fixture.git("--git-dir", str(fixture.remote), "rev-parse", "--verify", "refs/heads/ledger-probe")
        assert remote.returncode != 0
    else:
        assert collected["canonical_keys"] == []
        assert result.returncode == 0, output
        logs = list((Path(fixture.environment["COLLECTION_OBSERVED_ROOT"]) / "collect-check").glob("*.log"))
        assert len(logs) == 1
        assert "3 tests collected" in logs[0].read_text(encoding="utf-8") and "Collection OK" in output
        remote = fixture.git("--git-dir", str(fixture.remote), "rev-parse", "--verify", "refs/heads/ledger-probe")
        assert remote.returncode == 0
        assert remote.stdout == fixture.git("rev-parse", "HEAD").stdout


def test_hook_fixture_repr_keeps_owned_environment_private(tmp_path: Path) -> None:
    environment = {
        "GLUDD_FAKE_PRIVATE_CARRIER": "owned-hook-private-repr-marker",
        "GLUDD_FAKE_RETAINED_CARRIER": "owned-hook-retained-repr-marker",
    }
    fixture = HookFixture(
        tmp_path / "main", tmp_path / "linked", tmp_path / "remote.git",
        "owned-native-git", environment, tmp_path / "hook", tmp_path / "collection",
    )

    rendered = repr(fixture)

    assert all(name not in rendered and value not in rendered for name, value in environment.items())
    assert fixture.environment is environment
    assert str(fixture.main) in rendered and str(fixture.linked) in rendered
    assert str(fixture.hook_snapshot) in rendered and str(fixture.collection_snapshot) in rendered
