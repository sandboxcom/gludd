#!/usr/bin/env python3
"""Self-improvement E2E — tests gludd improving itself in an isolated worktree."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


class _GatewayResponse:
    """Thin wrapper ensuring response.content attribute exists."""

    def __init__(self, content: str, usage_metadata: dict[str, object] | None = None) -> None:
        self.content = content
        self.usage_metadata = usage_metadata or {}
        self.cost_estimate: float = 0.0
        self.model_name: str = ""
        self.raw_response: object = None
        self.tool_calls: list[dict[str, object]] | None = None
        self.correlation_id: str | None = None


def _build_e2e_gateway():
    """Build a real ModelGateway backed by Azure GPU if available.

    Falls back to a thin wrapper that calls the Azure endpoint directly
    when no full ModelGateway can be constructed. NEVER returns a fake.
    """
    try:
        from general_ludd.cloud.deploy_strategy import build_azure_gateway

        gw = build_azure_gateway()
        if gw is not None:
            return gw
    except Exception:
        pass

    try:
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        base_url = os.environ.get("AZURE_BASE_URL", "")
        if not base_url:
            return _DirectAzureGateway(os.environ.get("AZURE_API_KEY", ""))

        profile = ModelProfile(
            model_profile_id="azure_self_improve",
            provider="openai",
            model_name=os.environ.get("AZURE_MODEL", "qwen2.5-coder-7b"),
            api_base_alias="AZURE_BASE_URL",
            credential_alias="AZURE_API_KEY",
            enabled=True,
            api_metered=False,
        )
        return ModelGateway(profiles=[profile])
    except Exception:
        return _DirectAzureGateway(os.environ.get("AZURE_API_KEY", ""))


class _DirectAzureGateway:
    """Minimal gateway calling Azure endpoint directly via requests.

    Used as a last resort when no ModelGateway can be built.
    Produces real model responses — never identity transforms.
    """

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key
        self._base_url = os.environ.get("AZURE_BASE_URL", "").rstrip("/")
        self._model = os.environ.get("AZURE_MODEL", "qwen2.5-coder-7b")

    def call_model(self, profile_id: str, messages: list[dict[str, str]], **kwargs: Any) -> _GatewayResponse:
        if not self._base_url:
            raise RuntimeError("No Azure endpoint configured. Set AZURE_BASE_URL and AZURE_API_KEY.")
        import json as _json
        import urllib.request as _urllib

        body = _json.dumps(
            {
                "model": self._model,
                "messages": messages,
                "max_tokens": 4096,
                "temperature": 0.7,
            }
        ).encode("utf-8")

        req = _urllib.Request(
            f"{self._base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}" if self._api_key else "",
            },
        )
        try:
            with _urllib.urlopen(req, timeout=120) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Azure gateway call failed: {exc}") from exc

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        content = str(msg.get("content", ""))

        if not content:
            raise RuntimeError("Azure returned empty content — cannot improve")

        return _GatewayResponse(
            content=content,
            usage_metadata=data.get("usage", {}),
        )

    def complete(self, prompt: str) -> _GatewayResponse:
        return self.call_model("azure_self_improve", [{"role": "user", "content": prompt}])


def _run_git(worktree: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=str(worktree),
        timeout=60,
    )


def _get_worktree_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return Path(result.stdout.strip())


def create_worktree() -> tuple[Path, str]:
    branch = f"gludd-self-improve-e2e-{int(time.time())}"
    worktree_path = Path("/tmp/gludd-self-improve-test")

    proc = subprocess.run(
        ["make", "agent-worktree", f"BRANCH={branch}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        if "already exists" in proc.stderr or "already exists" in proc.stdout:
            subprocess.run(
                ["make", "agent-cleanup", f"BRANCH={branch}"],
                capture_output=True,
                timeout=30,
            )
            proc = subprocess.run(
                ["make", "agent-worktree", f"BRANCH={branch}"],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )

    for line in (proc.stdout or "").splitlines():
        if "WORKTREE_PATH=" in line:
            worktree_path = Path(line.split("=", 1)[1].strip())
            break

    if not worktree_path.exists():
        worktree_path = Path("/tmp/gludd-self-improve-test")
        worktree_path.mkdir(parents=True, exist_ok=True)

    return worktree_path, branch


def cleanup_worktree(worktree_path: Path, branch: str) -> None:
    subprocess.run(
        ["make", "agent-cleanup", f"BRANCH={branch}"],
        capture_output=True,
        timeout=30,
    )


def run_improvement(worktree_path: Path, target_name: str, target_def: dict[str, str]) -> dict[str, Any]:
    sys.path.insert(0, str(worktree_path / "src"))
    try:
        from general_ludd.self_improve.evaluator import SelfImproveEvaluator
    finally:
        sys.path.pop(0)

    gateway = _build_e2e_gateway()
    evaluator = SelfImproveEvaluator(
        gateway=gateway,
        test_file=target_def["test_file"],
        component_file=target_def["component_file"],
        provider=target_def["provider"],
        repo_root=str(worktree_path),
        max_attempts=1,
        budget_usd=5.0,
    )
    return evaluator.report()


def commit_improvement(
    worktree_path: Path, result: dict[str, Any], target_name: str, component_file: str
) -> str | None:
    if not result.get("improvement_accepted"):
        return None

    _run_git(worktree_path, ["add", component_file])
    msg = f"self-improve(e2e): {target_name} — "
    deltas = result.get("deltas", {})
    msg += f"pass_rate {deltas.get('pass_rate', 0):+.2f}"
    proc = _run_git(
        worktree_path,
        ["commit", "-m", msg, "--allow-empty"],
    )
    if proc.returncode != 0:
        return None
    log = _run_git(worktree_path, ["log", "-1", "--format=%H"])
    return log.stdout.strip()[:10]


def print_summary(results: list[dict[str, Any]]) -> None:
    header = f"{'Component':<30} {'Provider':<10} {'Base P/T':>10} {'Imp P/T':>10} "
    header += f"{'Delta':>8} {'Acc?':>5} {'Commit':>10}"
    print(header)
    print("-" * len(header))

    improved_count = 0
    for r in results:
        baseline = r.get("baseline", {})
        improved = r.get("improved_metrics", {})
        deltas = r.get("deltas", {})
        accepted = "YES" if r.get("improvement_accepted") else "NO"
        if accepted == "YES":
            improved_count += 1
        commit = r.get("commit", "")
        base_pt = f"{baseline.get('test_pass', 0)}/{baseline.get('test_count', 0)}"
        imp_pt = f"{improved.get('test_pass', 0)}/{improved.get('test_count', 0)}"
        delta = f"{deltas.get('pass_rate', 0):+.2f}"
        print(
            f"{r['component']:<30} {r['provider']:<10} {base_pt:>10} {imp_pt:>10} {delta:>8} {accepted:>5} {commit:>10}"
        )

    print(f"\n{improved_count}/{len(results)} components improved")


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-improvement E2E test")
    parser.add_argument("--target", default="all", help="Target component name")
    parser.add_argument("--all", action="store_true", help="Run all targets")
    parser.add_argument("--worktree", action="store_true", help="Use isolated git worktree")
    parser.add_argument("--merge", action="store_true", help="Merge worktree branch after completion")
    args = parser.parse_args()

    worktree_path = _get_worktree_root()
    branch_name = ""
    if args.worktree:
        worktree_path, branch_name = create_worktree()
    else:
        # Validate that we're inside a git worktree or main checkout
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            print("ERROR: Not in a git repository and --worktree not set", file=sys.stderr)
            sys.exit(1)

    # Load target definitions
    sys.path.insert(0, str(worktree_path / "src"))
    try:
        from general_ludd.self_improve.evaluator import SELF_IMPROVE_TARGETS
    finally:
        sys.path.pop(0)

    targets_to_run: dict[str, dict[str, str]] = {}
    if args.all or args.target == "all":
        targets_to_run = dict(SELF_IMPROVE_TARGETS)
    else:
        if args.target not in SELF_IMPROVE_TARGETS:
            available = ", ".join(sorted(SELF_IMPROVE_TARGETS))
            print(f"ERROR: Unknown target '{args.target}'. Available: {available}", file=sys.stderr)
            sys.exit(1)
        targets_to_run = {args.target: SELF_IMPROVE_TARGETS[args.target]}

    results: list[dict[str, Any]] = []
    for target_name, target_def in targets_to_run.items():
        component_file = target_def["component_file"]
        component_path = worktree_path / component_file
        if not component_path.exists():
            print(f"SKIP: {target_name} — component file {component_file} not found")
            results.append(
                {
                    "component": target_name,
                    "provider": target_def["provider"],
                    "baseline": {},
                    "improved_metrics": {},
                    "deltas": {},
                    "improvement_accepted": False,
                    "errors": [f"component_file not found: {component_file}"],
                    "commit": "",
                }
            )
            continue

        print(f"\n=== Running: {target_name} ({target_def['description']}) ===")
        try:
            report = run_improvement(worktree_path, target_name, target_def)
            report["commit"] = (
                commit_improvement(
                    worktree_path,
                    report,
                    target_name,
                    component_file,
                )
                if report.get("improvement_accepted")
                else ""
            )
            results.append(report)
        except Exception as exc:
            print(f"FAIL: {target_name} — {exc}")
            results.append(
                {
                    "component": target_name,
                    "provider": target_def["provider"],
                    "baseline": {},
                    "improved_metrics": {},
                    "deltas": {},
                    "improvement_accepted": False,
                    "errors": [str(exc)],
                    "commit": "",
                }
            )

    print("\n" + "=" * 60)
    print_summary(results)

    if args.merge and branch_name:
        print(f"\nMerging worktree branch {branch_name} into development...")
        proc = subprocess.run(
            ["make", "agent-merge-dev", f"BRANCH={branch_name}"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            print("Merge succeeded.")
            subprocess.run(
                ["make", "agent-cleanup", f"BRANCH={branch_name}"],
                capture_output=True,
                timeout=30,
            )
        else:
            print(f"Merge failed: {proc.stderr}")

    elif args.worktree and branch_name:
        cleanup_worktree(worktree_path, branch_name)

    failed = [r for r in results if r.get("errors")]
    if failed:
        print(f"\n{len(failed)} target(s) had errors")
        sys.exit(1)


if __name__ == "__main__":
    main()
