"""Failing-first contracts for the application resource-ownership gate."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from scripts.check_resource_ownership import (
    ResourceEvidence,
    load_inventory,
    main,
    scan_paths,
    validate_inventory,
    write_inventory,
)


def _scan(tmp_path: Path, source: str) -> list[ResourceEvidence]:
    app = tmp_path / "app"
    app.mkdir()
    module = app / "sample.py"
    module.write_text(source, encoding="utf-8")
    return scan_paths([app], root=tmp_path)


@pytest.mark.parametrize(
    ("source", "kind", "teardown_fragment"),
    [
        (
            """\
import subprocess

def run() -> None:
    proc = subprocess.Popen([\"worker\"])
    try:
        proc.poll()
    finally:
        proc.terminate()
        proc.wait()
""",
            "process",
            "proc.terminate",
        ),
        (
            """\
import asyncio

async def run() -> None:
    task = asyncio.create_task(asyncio.sleep(1))
    try:
        await asyncio.sleep(0)
    finally:
        task.cancel()
        await task
""",
            "async-task",
            "task.cancel",
        ),
        (
            """\
import httpx

async def run() -> None:
    async with httpx.AsyncClient() as client:
        await client.get(\"http://127.0.0.1\")
""",
            "client",
            "context-manager-exit",
        ),
        (
            """\
import tempfile

def run() -> None:
    with tempfile.TemporaryDirectory() as path:
        print(path)
""",
            "temp-artifact",
            "context-manager-exit",
        ),
        (
            """\
def run() -> None:
    endpoint = EndpointLifecycle(model_id=\"m\")
    endpoint.start()
    try:
        use(endpoint)
    finally:
        endpoint.stop()
""",
            "service",
            "endpoint.stop",
        ),
    ],
)
def test_scan_records_exact_acquisition_to_teardown_evidence(
    tmp_path: Path,
    source: str,
    kind: str,
    teardown_fragment: str,
) -> None:
    findings = _scan(tmp_path, source)

    assert len(findings) == 1
    assert findings[0].kind == kind
    assert findings[0].owned is True
    assert teardown_fragment in findings[0].teardown
    assert findings[0].source_hash


def test_task_group_is_structured_teardown_evidence(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """\
import asyncio

async def run() -> None:
    async with asyncio.TaskGroup() as group:
        group.create_task(asyncio.sleep(1))
""",
    )

    assert len(findings) == 1
    assert findings[0].kind == "async-task"
    assert findings[0].teardown == "TaskGroup.__aexit__"


def test_class_task_registry_drain_follows_tuple_snapshot_alias(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """\
import asyncio
from typing import Any

class Owner:
    def __init__(self) -> None:
        self.tasks: set[asyncio.Task[Any]] = set()

    def start(self) -> None:
        task = asyncio.create_task(do_work())
        self.tasks.add(task)

    async def shutdown(self) -> None:
        snapshot = tuple(self.tasks)
        done, pending = await asyncio.wait(snapshot, timeout=1.0)
        await asyncio.gather(*done, return_exceptions=True)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.tasks.clear()
""",
    )

    assert len(findings) == 1
    assert findings[0].owned is True
    assert "asyncio.wait(snapshot" in findings[0].teardown


@pytest.mark.parametrize(
    "source",
    [
        """\
import subprocess

def run() -> None:
    proc = subprocess.Popen(["worker"])
    do_work()
    proc.wait()
""",
        """\
import asyncio

_TASKS = set()

async def run() -> None:
    task = asyncio.create_task(do_work())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
""",
        """\
import httpx

def run() -> None:
    client = httpx.Client()
    do_work()
    client.close()
""",
        """\
import tempfile
from pathlib import Path

def run() -> None:
    path = tempfile.mkdtemp()
    do_work()
    Path(path).rmdir()
""",
    ],
)
def test_success_only_cleanup_is_not_ownership_evidence(
    tmp_path: Path,
    source: str,
) -> None:
    finding = _scan(tmp_path, source)[0]

    assert finding.owned is False
    assert finding.teardown == ""


def test_class_owned_artifact_can_transfer_from_local_to_close_method(tmp_path: Path) -> None:
    finding = _scan(
        tmp_path,
        """\
import os
import tempfile

class Owner:
    def start(self) -> None:
        fd, ready_path = tempfile.mkstemp()
        os.close(fd)
        self._ready_path = ready_path

    def close(self) -> None:
        for path in (self._ready_path,):
            os.unlink(path)
""",
    )[0]

    assert finding.owned is True
    assert "os.unlink(path)" in finding.teardown


def test_application_state_transfer_resolves_registered_shutdown_owner(tmp_path: Path) -> None:
    finding = _scan(
        tmp_path,
        """\
import contextlib

def install_lifespan(app):
    @contextlib.asynccontextmanager
    async def lifespan(owner):
        try:
            yield
        finally:
            manager = getattr(owner.state, "_manager", None)
            if manager is not None:
                await manager.stop_all()
    app.router.lifespan_context = lifespan

def register(app):
    manager = LocalInferenceManager()
    app.state._manager = manager
    install_lifespan(app)
""",
    )[0]

    assert finding.owned is True
    assert "manager.stop_all" in finding.teardown


def test_atomic_replace_with_failure_unlink_owns_temp_artifact(tmp_path: Path) -> None:
    finding = _scan(
        tmp_path,
        """\
import contextlib
import os
import tempfile

def write(path: str) -> None:
    fd, temporary = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("ok")
        os.replace(temporary, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
""",
    )[0]

    assert finding.owned is True
    assert finding.teardown == "atomic-replace-or-unlink"


def test_unowned_resource_fails_even_when_inventory_mentions_it(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """\
import httpx

def run() -> None:
    client = httpx.Client()
    client.get(\"http://127.0.0.1\")
""",
    )
    inventory = {finding.key(): finding for finding in findings}

    errors = validate_inventory(findings, inventory)

    assert any("unowned resource" in error for error in errors)


def test_inventory_fails_on_new_and_stale_exact_evidence(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """\
import tempfile

def run() -> None:
    with tempfile.TemporaryDirectory() as path:
        print(path)
""",
    )
    stale = ResourceEvidence(
        path="app/removed.py",
        line=9,
        column=4,
        kind="temp-artifact",
        owner="run",
        acquisition="tempfile.TemporaryDirectory()",
        teardown="context-manager-exit",
        source_hash="0" * 64,
        owned=True,
    )

    errors = validate_inventory(findings, {stale.key(): stale})

    assert any(error.startswith("new resource:") for error in errors)
    assert any(error.startswith("stale inventory:") for error in errors)


def test_inventory_loader_rejects_duplicate_or_unowned_entries(tmp_path: Path) -> None:
    entry = {
        "path": "app/sample.py",
        "line": 4,
        "column": 4,
        "kind": "client",
        "owner": "run",
        "acquisition": "httpx.Client()",
        "teardown": "",
        "source_hash": "a" * 64,
        "owned": False,
    }
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps({"schema_version": 1, "resources": [entry, entry]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"unowned|duplicate"):
        load_inventory(path)


@pytest.mark.parametrize(
    ("source", "teardown"),
    [
        (
            """\
import asyncio

async def run() -> None:
    await asyncio.create_task(do_work())
""",
            "await task completion",
        ),
        (
            """\
def build():
    return LocalInferenceManager()
""",
            "ownership-transfer:return",
        ),
        (
            """\
import subprocess

def start() -> int:
    proc = subprocess.Popen(["worker"], start_new_session=True)
    persist(proc.pid)
    return proc.pid
""",
            "persisted-detached-pid",
        ),
        (
            """\
import asyncio

_tasks = set()

async def run() -> None:
    task = asyncio.create_task(do_work())
    _tasks.add(task)

async def close() -> None:
    for task in _tasks:
        task.cancel()
        await task
""",
            "await task",
        ),
        (
            """\
import asyncio
import httpx

async def run() -> None:
    client = httpx.Client()
    try:
        await use(client)
    finally:
        await asyncio.to_thread(client.close)
""",
            "asyncio.to_thread(client.close)",
        ),
        (
            """\
from contextlib import asynccontextmanager
import httpx

@asynccontextmanager
async def lifespan(app):
    failure = None
    try:
        yield
    except BaseException as exc:
        failure = exc
    client = httpx.Client()
    client.close()
    if failure is not None:
        raise failure
""",
            "client.close()",
        ),
    ],
)
def test_transfer_and_deferred_shutdown_patterns_are_owned(
    tmp_path: Path,
    source: str,
    teardown: str,
) -> None:
    findings = _scan(tmp_path, source)

    assert findings
    assert findings[0].owned is True
    assert teardown in findings[0].teardown


def test_inventory_write_and_cli_round_trip(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = source_dir / "owned.py"
    source.write_text(
        """\
import tempfile

def run() -> None:
    with tempfile.TemporaryDirectory() as path:
        print(path)
""",
        encoding="utf-8",
    )
    inventory = tmp_path / "config" / "inventory.json"

    assert main(
        [
            "--root",
            str(tmp_path),
            "--inventory",
            str(inventory),
            "--write-inventory",
            str(source),
        ]
    ) == 0
    assert "INVENTORY_WRITTEN resources=1" in capsys.readouterr().out
    assert main(
        [
            "--root",
            str(tmp_path),
            "--inventory",
            str(inventory),
            str(source_dir),
        ]
    ) == 0
    assert "RESOURCE_OWNERSHIP_PASS resources=1" in capsys.readouterr().out
    assert load_inventory(inventory)


def test_cli_reports_inventory_drift_and_input_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "owned.py"
    source.write_text(
        "import tempfile\nwith tempfile.TemporaryDirectory() as path:\n    print(path)\n",
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory.json"
    findings = scan_paths([source], root=tmp_path)
    write_inventory(inventory, findings)
    source.write_text("print('removed')\n", encoding="utf-8")

    assert main(
        ["--root", str(tmp_path), "--inventory", str(inventory), str(source)]
    ) == 1
    assert "stale inventory:" in capsys.readouterr().err

    assert main(
        [
            "--root",
            str(tmp_path),
            "--inventory",
            str(tmp_path / "missing.json"),
            str(source),
        ]
    ) == 2
    assert "RESOURCE_OWNERSHIP_FAIL" in capsys.readouterr().err


def test_inventory_writer_refuses_unowned_and_loader_rejects_schema(
    tmp_path: Path,
) -> None:
    unowned = _scan(
        tmp_path,
        "import httpx\nclient = httpx.Client()\nclient.get('http://localhost')\n",
    )
    with pytest.raises(ValueError, match="refusing to inventory 1 unowned"):
        write_inventory(tmp_path / "inventory.json", unowned)

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 2, "resources": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version 1"):
        load_inventory(invalid)


def test_scan_paths_accepts_file_skips_cache_and_rejects_missing(tmp_path: Path) -> None:
    source = tmp_path / "owned.py"
    source.write_text(
        "import tempfile\nwith tempfile.TemporaryDirectory() as path:\n    print(path)\n",
        encoding="utf-8",
    )
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "ignored.py").write_text("client = HttpClient()\n", encoding="utf-8")

    assert len(scan_paths([source], root=tmp_path)) == 1
    assert len(scan_paths([tmp_path], root=tmp_path)) == 1
    with pytest.raises(FileNotFoundError, match="scan path does not exist"):
        scan_paths([tmp_path / "missing"], root=tmp_path)


def test_inventory_loader_rejects_non_object_resource(tmp_path: Path) -> None:
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps({"schema_version": 1, "resources": ["not-an-object"]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="resources must be objects"):
        load_inventory(path)


def test_secrets_filter_excludes_only_generated_ownership_inventory() -> None:
    baseline = json.loads(Path(".secrets.baseline").read_text(encoding="utf-8"))
    patterns = [
        pattern
        for item in baseline["filters_used"]
        if item["path"] == "detect_secrets.filters.regex.should_exclude_file"
        for pattern in item["pattern"]
    ]

    assert any(
        re.search(pattern, "config/resource_ownership_inventory.json")
        for pattern in patterns
    )
    assert not any(
        re.search(pattern, "config/unrelated_inventory.json")
        for pattern in patterns
    )


def test_secrets_baseline_regeneration_preserves_exact_inventory_filter() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    assert (
        "SECRETS_EXCLUDE_FILES ?= "
        "sandboxcom_github_rsa|sandboxcom_github_rsa.pub|"
        r"^config/resource_ownership_inventory\.json$"
    ) in makefile
    assert makefile.count("--exclude-files '$(SECRETS_EXCLUDE_FILES)'") == 2
