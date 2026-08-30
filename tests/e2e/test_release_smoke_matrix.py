"""Release smoke matrix for provider safety, OpenCode boot, and readiness guards.

These checks are intentionally credential-free.  Provider live paths are
exercised only far enough to prove missing credentials fail before a network
request; the OpenCode verifier runs locally against the checked-in plugin tree.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = ROOT / "scripts" / "provider_smoke_harness.py"
VERIFIER_PATH = ROOT / ".opencode" / "scripts" / "verify-plugins.mjs"
OPENCODE_CONFIG = ROOT / "opencode.json"

sys.path.insert(0, str(ROOT / "scripts"))
import release_readiness as readiness  # noqa: E402

_HARNESS_SPEC = importlib.util.spec_from_file_location("release_smoke_harness", HARNESS_PATH)
assert _HARNESS_SPEC and _HARNESS_SPEC.loader
harness = importlib.util.module_from_spec(_HARNESS_SPEC)
_HARNESS_SPEC.loader.exec_module(harness)


@pytest.mark.parametrize(
    ("provider", "environment"),
    [
        ("azure", {"AZURE_SUBSCRIPTION_ID": "sub-smoke", "AZURE_TENANT_ID": "tenant-smoke"}),
        ("runpod", {}),
    ],
)
def test_dry_run_never_opens_network(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    environment: dict[str, str],
) -> None:
    """Configuration validation must be usable with no credentials or billing."""

    def network_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run provider validation attempted network access")

    monkeypatch.setattr(harness.urllib.request, "urlopen", network_forbidden)
    result = harness.run_harness(provider, environment, live=False)

    assert result["ok"] is True
    assert result["mode"] == "dry-run"
    assert result["checks"] == {"mode": "configuration"}


@pytest.mark.parametrize("provider", ["azure", "runpod"])
def test_live_mode_rejects_missing_credentials_before_network(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Live mode must fail closed before contacting a provider or billing API."""

    calls = 0

    def network_forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise AssertionError("credential validation reached the network")

    monkeypatch.setattr(harness.urllib.request, "urlopen", network_forbidden)
    with pytest.raises(harness.HarnessConfigError):
        harness.run_harness(provider, {}, live=True)
    assert calls == 0


def test_live_provider_request_failure_is_reported_without_resource_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider outage is a failed smoke test, never a successful deployment."""

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.URLError("offline smoke environment")

    monkeypatch.setattr(harness.urllib.request, "urlopen", unavailable)
    environment = {
        "AZURE_SUBSCRIPTION_ID": "sub-smoke",
        "AZURE_TENANT_ID": "tenant-smoke",
        "AZURE_CLIENT_ID": "client-smoke",
        "AZURE_CLIENT_SECRET": "secret-smoke",
    }
    with pytest.raises(harness.HarnessConfigError, match="Azure credential validation failed"):
        harness.run_harness("azure", environment, live=True)


def test_opencode_release_config_resolves_registered_plugins() -> None:
    """Every configured plugin must exist and the auto-discovery directory stays clean."""

    config = json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))
    entries = config.get("plugin", [])
    assert entries
    paths = [entry if isinstance(entry, str) else entry[0] for entry in entries]
    assert len(paths) == len(set(paths))
    missing = [path for path in paths if not (ROOT / path).is_file()]
    assert not missing, f"missing OpenCode plugins: {missing}"
    plugin_dir = ROOT / ".opencode" / "plugin"
    assert not list(plugin_dir.glob("*_exports.ts"))
    assert "./.opencode/plugin/enforce-release-deadline.ts" in paths


def test_opencode_plugin_verifier_passes_when_node_is_available() -> None:
    """Run the same dynamic import/factory check used by the boot gate."""

    if shutil.which("node") is None:
        pytest.skip("node is not available in this environment")
    result = subprocess.run(
        ["node", "--experimental-strip-types", str(VERIFIER_PATH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout)
    assert payload.get("failures", []) == []


def test_verified_claims_plugin_runtime_blocks_low_coverage_completion_claim() -> None:
    """The loaded enforcement plugin must block a low-coverage completion claim."""

    if shutil.which("node") is None:
        pytest.skip("node is not available in this environment")
    helpers = ROOT / ".opencode" / "lib" / "plugin_test_exports.ts"
    script = f"""
const mod = await import({json.dumps(str(helpers))})
console.log(JSON.stringify({{ blocked: mod.shouldBlockCoverageClaim("final e2e push at 84%") }}))
"""
    env = os.environ.copy()
    env["OPENCODE_SUBAGENT"] = ""
    env["GLUDD_VERIFIED_CLAIMS_ENFORCE"] = "1"
    env["GLUDD_DISENGAGE_PATH"] = f"/tmp/gludd-release-smoke-disengage-{os.getpid()}.json"
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"blocked": True}


def test_release_version_check_uses_canonical_helper() -> None:
    """The release matrix observes the repository's canonical version guard."""

    valid, detail = readiness._version_check(readiness._run, ROOT)
    assert valid, detail


def test_release_tasks_track_only_current_release_items(tmp_path: Path) -> None:
    """Release preflight derives task selection from the canonical release policy."""

    tag = readiness.DEFAULT_RELEASE_TAG
    prefix = readiness.RELEASE_TASK_PREFIXES[tag][0]
    release_action = next(iter(readiness.RELEASE_ACTION_TASKS[tag]))
    pending_task = f"{prefix}999"
    completed_task = f"{prefix}998"
    assert pending_task != release_action
    (tmp_path / "TASKS.md").write_text(
        f"- [ ] {pending_task} — configure smoke matrix\n"
        f"- [x] {completed_task} — already certified\n"
        f"- [ ] {release_action} — release action handled after readiness\n"
        "- [ ] OTHER-ITEM — unrelated follow-up\n",
        encoding="utf-8",
    )
    assert readiness._incomplete_tasks(tmp_path, tag=tag) == [pending_task]
