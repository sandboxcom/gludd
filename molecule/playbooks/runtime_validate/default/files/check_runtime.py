#!/usr/bin/env python3
"""Validate runtime components: worker app, event loop, ansible runners, gunicorn.

argv[1] = project src dir. Prints one '<NAME> OK'/'<NAME> FAIL' line per check
and exits non-zero on the first failure so the molecule task fails loudly.
"""
import os
import sys

src = sys.argv[1] if len(sys.argv) > 1 else "src"
# argv[2] = project root (so we can locate playbooks/noop.yml regardless of cwd).
project_root = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
sys.path.insert(0, src)


def _fail(name: str, exc: Exception) -> None:
    print(f"{name} FAIL: {exc}")
    sys.exit(1)


# Worker app factory
try:
    from general_ludd.worker.app import create_app

    create_app()
    print("WORKER OK: app factory created")
except Exception as exc:  # noqa: BLE001
    _fail("WORKER", exc)

# Event loop
try:
    from general_ludd.event_loop.loop import EventLoop

    EventLoop()
    print("EVENT_LOOP OK: instance created")
except Exception as exc:  # noqa: BLE001
    _fail("EVENT_LOOP", exc)

# Ansible runner adapter
try:
    from general_ludd.ansible.runner import AnsibleRunnerAdapter

    runner = AnsibleRunnerAdapter()
    dirs = runner.prepare_job_dirs("TEST-MOLECULE-JOB")
    print(f"RUNNER OK: dirs={list(dirs.keys())}")
except Exception as exc:  # noqa: BLE001
    _fail("RUNNER", exc)

# Core ansible runner: assert it IMPORTS and instantiates + resolves the noop
# playbook path. We do NOT actually run a nested ansible-playbook here: molecule
# exports ANSIBLE_* env (restricted collection/plugin paths) that leak into a
# nested ansible-runner subprocess and break ansible.builtin resolution. The
# real nested-execution path is covered by tests/integration (CoreAnsibleRunner)
# in a clean env. Here we keep an honest import+resolve smoke check.
try:
    from general_ludd.ansible.core_runner import CoreAnsibleRunner

    runner = CoreAnsibleRunner()
    playbook = os.path.join(project_root, "playbooks", "noop.yml")
    if not os.path.isfile(playbook):
        raise FileNotFoundError(playbook)
    print(f"CORE_RUNNER OK: instantiated, playbook resolved={os.path.basename(playbook)}")
except Exception as exc:  # noqa: BLE001
    _fail("CORE_RUNNER", exc)

# Gunicorn config
try:
    from general_ludd.worker.gunicorn_conf import (
        max_requests,
        post_fork,
        timeout,
        worker_class,
        workers,
    )

    assert isinstance(workers, int)
    assert isinstance(timeout, int)
    assert isinstance(max_requests, int)
    assert isinstance(worker_class, str) and worker_class
    assert callable(post_fork)
    print(f"GUNICORN OK: workers={workers} timeout={timeout} worker_class={worker_class}")
except Exception as exc:  # noqa: BLE001
    _fail("GUNICORN", exc)

print("ALL_RUNTIME_CHECKS OK")
