"""Deep GitHub Actions integrity tests across all .github/workflows/*.yml.

Covers: workflow discovery, trigger validity, SHA-pinned action refs,
deprecated node12 detection, composite action structure, YAML parseability,
concurrency groups, permission scoping, job timeouts, and step naming.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import yaml

WORKFLOW_DIR = pathlib.Path(__file__).parent.parent.parent / ".github" / "workflows"
ACTIONS_DIR = pathlib.Path(__file__).parent.parent.parent / ".github" / "actions"
MOLECULE_NODE24_ACTIONS = {
    "actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd",
    "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405",
    "astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39",
}

VALID_EVENTS: frozenset[str] = frozenset(
    {
        "branch_protection_rule",
        "check_run",
        "check_suite",
        "create",
        "delete",
        "deployment",
        "deployment_status",
        "discussion",
        "discussion_comment",
        "fork",
        "gollum",
        "issue_comment",
        "issues",
        "label",
        "merge_group",
        "member",
        "milestone",
        "page_build",
        "project",
        "project_card",
        "project_column",
        "public",
        "pull_request",
        "pull_request_comment",
        "pull_request_review",
        "pull_request_review_comment",
        "pull_request_target",
        "push",
        "registry_package",
        "release",
        "repository_dispatch",
        "schedule",
        "status",
        "watch",
        "workflow_call",
        "workflow_dispatch",
        "workflow_run",
    }
)

DEPRECATED_NODE12_ACTIONS: frozenset[str] = frozenset(
    {
        "actions/checkout@v1",
        "actions/checkout@v2",
        "actions/setup-python@v1",
        "actions/setup-python@v2",
        "actions/setup-node@v1",
        "actions/setup-node@v2",
        "actions/upload-artifact@v1",
        "actions/upload-artifact@v2",
        "actions/download-artifact@v1",
        "actions/download-artifact@v2",
        "actions/cache@v1",
        "actions/cache@v2",
        "docker/setup-buildx-action@v1",
        "docker/login-action@v1",
        "docker/build-push-action@v1",
        "docker/build-push-action@v2",
        "docker/metadata-action@v2",
        "docker/metadata-action@v3",
    }
)

SHA_REF_RE = re.compile(r"@[0-9a-f]{40}$")
FLOATING_TAG_RE = re.compile(r"@v(\d+)$")


def _discover_workflow_paths() -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    if WORKFLOW_DIR.is_dir():
        for f in sorted(WORKFLOW_DIR.iterdir()):
            if f.suffix in (".yml", ".yaml"):
                paths.append(f)
    return paths


def _discover_action_paths() -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    if ACTIONS_DIR.is_dir():
        for f in sorted(ACTIONS_DIR.rglob("action.{yml,yaml}")):
            paths.append(f)
    return paths


def _load_yaml(path: pathlib.Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.name} must be a YAML mapping"
    return data


def _iter_steps(job_def: dict) -> list[dict]:
    steps: list[dict] = []
    for step in job_def.get("steps", []):
        if isinstance(step, dict):
            steps.append(step)
    return steps


def _extract_uses_refs(wf: dict) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    for job_name, job_def in wf.get("jobs", {}).items():
        for step_idx, step in enumerate(_iter_steps(job_def)):
            raw_uses = step.get("uses", "")
            if raw_uses:
                results.append((job_name, step.get("id", f"step-{step_idx}"), raw_uses))
    return results


def _resolve_on(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("on") or data.get(True, {})  # type: ignore[arg-type]
    if isinstance(raw, dict):
        return raw
    return {}


class TestWorkflowDiscovery:
    def test_at_least_one_workflow_found(self):
        paths = _discover_workflow_paths()
        assert len(paths) >= 1, "No .github/workflows/*.yml files found"

    def test_all_discovered_workflows_are_valid_yaml(self):
        for path in _discover_workflow_paths():
            data = yaml.safe_load(path.read_text())
            assert isinstance(data, dict), f"{path.name}: must be a YAML mapping"

    def test_every_workflow_has_a_name(self):
        for path in _discover_workflow_paths():
            data = _load_yaml(path)
            assert "name" in data, f"{path.name}: missing 'name' key"
            assert len(data["name"]) > 0, f"{path.name}: 'name' is empty"

    def test_build_yml_exists(self):
        paths = {p.name for p in _discover_workflow_paths()}
        assert "build.yml" in paths

    def test_pages_yml_exists(self):
        paths = {p.name for p in _discover_workflow_paths()}
        assert "pages.yml" in paths

    def test_molecule_yml_exists(self):
        paths = {p.name for p in _discover_workflow_paths()}
        assert "molecule.yml" in paths


class TestWorkflowTriggers:
    def test_all_on_events_are_valid(self):
        for path in _discover_workflow_paths():
            data = _load_yaml(path)
            on_block = _resolve_on(data)
            for event in on_block:
                if isinstance(event, str):
                    assert event in VALID_EVENTS, f"{path.name}: unknown trigger event '{event}'"

    def test_build_yml_triggers_on_push(self):
        on_block = _resolve_on(_load_yaml(WORKFLOW_DIR / "build.yml"))
        assert "push" in on_block

    def test_build_yml_triggers_on_pull_request(self):
        on_block = _resolve_on(_load_yaml(WORKFLOW_DIR / "build.yml"))
        assert "pull_request" in on_block

    def test_build_yml_triggers_on_workflow_dispatch(self):
        on_block = _resolve_on(_load_yaml(WORKFLOW_DIR / "build.yml"))
        assert "workflow_dispatch" in on_block

    def test_build_yml_push_tags_include_v_star(self):
        on_block = _resolve_on(_load_yaml(WORKFLOW_DIR / "build.yml"))
        push = on_block.get("push", {})
        tags = push.get("tags", [])
        assert "v*" in tags, f"build.yml push.tags must include 'v*', got {tags!r}"

    def test_build_yml_push_branches_has_master(self):
        on_block = _resolve_on(_load_yaml(WORKFLOW_DIR / "build.yml"))
        push = on_block.get("push", {})
        branches = push.get("branches", [])
        assert "master" in branches

    def test_pages_yml_triggers_on_push_to_master(self):
        on_block = _resolve_on(_load_yaml(WORKFLOW_DIR / "pages.yml"))
        push = on_block.get("push", {})
        branches = push.get("branches", [])
        assert "master" in branches

    def test_molecule_yml_triggers_on_workflow_dispatch(self):
        on_block = _resolve_on(_load_yaml(WORKFLOW_DIR / "molecule.yml"))
        assert "workflow_dispatch" in on_block


class TestActionRefsPinnedToSha:
    def test_build_yml_all_uses_are_sha_pinned(self):
        data = _load_yaml(WORKFLOW_DIR / "build.yml")
        unpinned: list[str] = []
        for job, step, raw in _extract_uses_refs(data):
            if not SHA_REF_RE.search(raw):
                unpinned.append(f"  {job}/{step}: {raw}")
        assert not unpinned, "build.yml has action refs NOT pinned to a commit SHA:\n" + "\n".join(unpinned)

    def test_pages_yml_all_uses_are_sha_pinned(self):
        data = _load_yaml(WORKFLOW_DIR / "pages.yml")
        unpinned: list[str] = []
        for job, step, raw in _extract_uses_refs(data):
            if not SHA_REF_RE.search(raw):
                unpinned.append(f"  {job}/{step}: {raw}")
        assert not unpinned, "pages.yml has action refs NOT pinned to a commit SHA:\n" + "\n".join(unpinned)

    def test_molecule_yml_uses_pinned_node24_actions(self) -> None:
        """Molecule must not rely on GHE's deprecated-Node forced fallback."""
        data = _load_yaml(WORKFLOW_DIR / "molecule.yml")
        refs = {raw for _job, _step, raw in _extract_uses_refs(data)}

        assert refs == MOLECULE_NODE24_ACTIONS
        assert all(SHA_REF_RE.search(ref) for ref in refs)

    def test_all_workflows_use_sha_pinned_actions(self) -> None:
        """Every third-party workflow action is immutable."""
        failures: list[str] = []
        for path in _discover_workflow_paths():
            data = _load_yaml(path)
            for job, step, raw in _extract_uses_refs(data):
                if not SHA_REF_RE.search(raw):
                    failures.append(f"  {path.name}: {job}/{step}: {raw}")
        assert not failures, "Workflows have unpinned action refs:\n" + "\n".join(failures)

    def test_build_yml_shas_are_valid_hex(self):
        data = _load_yaml(WORKFLOW_DIR / "build.yml")
        for job, step, raw in _extract_uses_refs(data):
            m = SHA_REF_RE.search(raw)
            if m:
                sha = m.group(0).lstrip("@")
                assert len(sha) == 40, f"{job}/{step}: SHA '{sha}' is not 40 hex chars"
                assert re.fullmatch(r"[0-9a-f]{40}", sha), f"{job}/{step}: SHA '{sha}' is not hex"


class TestNoDeprecatedNode12:
    def test_no_deprecated_node12_actions_in_any_workflow(self):
        hits: list[str] = []
        for path in _discover_workflow_paths():
            data = _load_yaml(path)
            for job, step, raw in _extract_uses_refs(data):
                if raw in DEPRECATED_NODE12_ACTIONS:
                    hits.append(f"  {path.name}: {job}/{step}: {raw}")
                else:
                    action_name = raw.rsplit("@", 1)[0].lower()
                    ref_part = raw.rsplit("@", 1)[-1]
                    if ref_part in ("v1", "v2", "v3") and len(ref_part) < 40:
                        for dep in DEPRECATED_NODE12_ACTIONS:
                            dep_name = dep.rsplit("@", 1)[0].lower()
                            if action_name == dep_name:
                                hits.append(f"  {path.name}: {job}/{step}: {raw}")
                                break
        assert not hits, (
            "Deprecated node12 action references found:\n"
            + "\n".join(hits)
            + "\n\nNode12 is deprecated by GitHub. Use node20/node24 actions pinned to SHA."
        )

    def test_no_floating_version_references_below_v4(self):
        too_old: list[str] = []
        for path in _discover_workflow_paths():
            data = _load_yaml(path)
            for job, step, raw in _extract_uses_refs(data):
                m = FLOATING_TAG_RE.search(raw)
                if m and int(m.group(1)) < 4:
                    too_old.append(f"  {path.name}: {job}/{step}: {raw}")
        assert not too_old, (
            "Action refs with major version < v4 (node12/node16) detected:\n"
            + "\n".join(too_old)
            + "\nUse actions at v4+ (node20) or pin to SHA."
        )


class TestCompositeActions:
    def test_composite_actions_dir_exists_or_noop(self):
        if not ACTIONS_DIR.is_dir():
            return
        action_files = _discover_action_paths()
        assert len(action_files) == 0 or all(f.suffix in (".yml", ".yaml") for f in action_files), (
            "Composite action files must be .yml or .yaml"
        )

    def test_composite_actions_have_required_runs_section(self):
        for af in _discover_action_paths():
            data = _load_yaml(af)
            if "runs" in data:
                runs = data["runs"]
                assert "using" in runs, f"{af.name}: runs.using is required"
                assert runs["using"] in ("composite", "node20", "node24", "docker"), (
                    f"{af.name}: runs.using '{runs['using']}' not recognized"
                )
                if runs["using"] == "composite":
                    assert "steps" in runs, f"{af.name}: composite action must have runs.steps"


class TestConcurrencyAndPermissions:
    def test_build_yml_has_concurrency_group(self):
        data = _load_yaml(WORKFLOW_DIR / "build.yml")
        assert "group" in data.get("concurrency", {})

    def test_build_yml_has_permissions(self):
        data = _load_yaml(WORKFLOW_DIR / "build.yml")
        assert "permissions" in data

    def test_pages_yml_has_concurrency_group(self):
        data = _load_yaml(WORKFLOW_DIR / "pages.yml")
        concurrency = data.get("concurrency", {})
        assert "group" in concurrency
        assert concurrency["group"] == "pages"

    def test_molecule_yml_has_concurrency_group(self):
        data = _load_yaml(WORKFLOW_DIR / "molecule.yml")
        assert "group" in data.get("concurrency", {})

    def test_pages_yml_permissions_are_scoped(self):
        data = _load_yaml(WORKFLOW_DIR / "pages.yml")
        perms = data.get("permissions", {})
        assert perms.get("contents") == "read"
        assert "pages" in perms
        assert perms.get("id-token") == "write"


class TestJobTimeouts:
    def test_every_job_in_every_workflow_has_timeout(self):
        missing: list[str] = []
        for path in _discover_workflow_paths():
            data = _load_yaml(path)
            for job_name, job_def in data.get("jobs", {}).items():
                if "timeout-minutes" not in job_def:
                    missing.append(f"  {path.name}: {job_name}")
        assert not missing, "Jobs missing timeout-minutes:\n" + "\n".join(missing)

    def test_no_timeout_exceeds_120_minutes(self):
        excessive: list[str] = []
        for path in _discover_workflow_paths():
            data = _load_yaml(path)
            for job_name, job_def in data.get("jobs", {}).items():
                timeout = job_def.get("timeout-minutes", 0)
                if timeout > 120:
                    excessive.append(f"  {path.name}: {job_name} ({timeout} min)")
        assert not excessive, "Jobs exceed 120-minute timeout limit:\n" + "\n".join(excessive)

    def test_pages_deploy_has_timeout(self):
        data = _load_yaml(WORKFLOW_DIR / "pages.yml")
        deploy = data["jobs"]["deploy"]
        assert "timeout-minutes" in deploy, "pages.yml deploy must have timeout-minutes"


class TestJobStepNaming:
    def test_molecule_yml_steps_have_names(self):
        data = _load_yaml(WORKFLOW_DIR / "molecule.yml")
        unnamed: list[str] = []
        for job_name, job_def in data.get("jobs", {}).items():
            for step_idx, step in enumerate(_iter_steps(job_def)):
                raw_uses = step.get("uses", "")
                if raw_uses and "name" not in step:
                    unnamed.append(f"  {job_name} step {step_idx + 1} ({raw_uses})")
        assert not unnamed, "molecule.yml has unnamed action steps:\n" + "\n".join(unnamed)

    def test_pages_yml_steps_have_names(self):
        data = _load_yaml(WORKFLOW_DIR / "pages.yml")
        unnamed: list[str] = []
        for job_name, job_def in data.get("jobs", {}).items():
            for step_idx, step in enumerate(_iter_steps(job_def)):
                raw_uses = step.get("uses", "")
                if raw_uses and "name" not in step:
                    unnamed.append(f"  {job_name} step {step_idx + 1} ({raw_uses})")
        assert not unnamed, "pages.yml has unnamed action steps:\n" + "\n".join(unnamed)

    def test_no_duplicate_step_ids_in_same_job(self):
        duplicates: list[str] = []
        for path in _discover_workflow_paths():
            data = _load_yaml(path)
            for job_name, job_def in data.get("jobs", {}).items():
                ids = [s["id"] for s in _iter_steps(job_def) if isinstance(s.get("id"), str)]
                seen: set[str] = set()
                for sid in ids:
                    if sid in seen:
                        duplicates.append(f"  {path.name}: {job_name} duplicate id '{sid}'")
                    seen.add(sid)
        assert not duplicates, "Duplicate step IDs found:\n" + "\n".join(duplicates)
