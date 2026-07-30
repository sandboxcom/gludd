"""Helper discovery for the Git Release Captain (spec GRC-001 §4.3, §5.2).

``discover_helpers`` walks a repository filesystem looking for build, test,
deploy, debug, package, migrate, and rollback entry points. It returns a list of
:class:`HelperCandidate` records, each tagged with an ``authority`` class that
reflects the spec's priority chain:

    project authority > existing CI usage >
    maintained ecosystem standard > locally generated helper

Discovery is filesystem-only (no subprocess, no shell). It does not parse file
contents beyond recognising the filename; deeper contract extraction is a later
phase. The scan is intentionally broad: it is the ranker's job to narrow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["HelperCandidate", "HelperInput", "HelperOutput", "ScoreEvidence", "discover_helpers"]


# ---------------------------------------------------------------------------
# Data contract (spec §5.2 HelperCandidate — subset populated at discovery)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HelperInput:
    """Declared input to a helper invocation (spec §5.2)."""

    name: str
    required: bool = False
    secret: bool = False
    default: str = ""


@dataclass(frozen=True)
class HelperOutput:
    """Declared output of a helper invocation (spec §5.2)."""

    name: str
    path_or_channel: str = ""
    digestible: bool = False


@dataclass(frozen=True)
class ScoreEvidence:
    """Recorded score component for a single criterion (spec §5.2).

    Lives in the catalog module because ``HelperCandidate.score_evidence`` is
    part of the §5.2 contract; the ranker populates it.
    """

    criterion: str
    value: int  # 0..10
    source: str  # short explanation of how the value was derived


@dataclass(frozen=True)
class HelperCandidate:
    """A discovered build/test/deploy helper (spec §5.2).

    Fields populated at discovery time: ``id``, ``kind``, ``source_path``,
    ``authority``. The remaining spec fields (``invocation_id``, ``inputs``,
    ``outputs``, ``side_effects``, ``supports_dry_run``, ``supports_rollback``,
    ``observability``, ``score``, ``score_evidence``) carry safe defaults and
    are filled in by the ranker / contract extractor.
    """

    id: str
    kind: str  # build | test | deploy | debug | package | migrate | rollback | other
    source_path: str  # repository-relative path or package URL
    authority: str  # repository | ci-used | ecosystem | generated
    invocation_id: str = ""
    inputs: tuple[HelperInput, ...] = ()
    outputs: tuple[HelperOutput, ...] = ()
    side_effects: tuple[str, ...] = ()
    supports_dry_run: bool = False
    supports_rollback: bool = False
    observability: tuple[str, ...] = ()
    score: int = 0
    score_evidence: tuple[ScoreEvidence, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Discovery rules
# ---------------------------------------------------------------------------


# Map of (filename or glob) -> (kind, authority). Order matters only for
# readability; the scan walks each category in spec priority order.
_REPO_DOCS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "CONTRIBUTING.rst",
    "README.md",
    "README.rst",
    "DEVELOPMENT.md",
    "SECURITY.md",
    "RELEASING.md",
)

# Native entry points: owned by the project itself.
_NATIVE_BUILD = {
    "Makefile": "build",
    "tox.ini": "test",
    "noxfile.py": "test",
}

# Task runners that may use varied suffixes/prefixes.
_TASKFILE_NAMES = ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml")
_JUSTFILE_NAMES = ("justfile", "Justfile", ".justfile")

# Ecosystem manifest files: present in every project of that language.
_ECOSYSTEM_MANIFESTS = {
    "pyproject.toml": "package",
    "setup.py": "package",
    "package.json": "package",
    "Cargo.toml": "package",
    "go.mod": "package",
    "pom.xml": "package",
    "build.gradle": "package",
    "build.gradle.kts": "package",
}

# Deploy / infra files owned by the project.
_DEPLOY_FILES = {
    "Dockerfile": "deploy",
    "docker-compose.yml": "deploy",
    "docker-compose.yaml": "deploy",
}

# CI workflow directories and glob patterns.
_CI_DIRS = (".github/workflows", ".gitlab-ci", ".circleci")
_CI_FILE_SUFFIXES = (".yml", ".yaml")


def _repo_rel(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _stable_id(rel_path: str) -> str:
    """Stable ID derived from the repository-relative path."""
    return f"helper:{rel_path}"


def _scan_repo_docs(repo_root: Path) -> list[HelperCandidate]:
    out: list[HelperCandidate] = []
    for name in _REPO_DOCS:
        candidate_path = repo_root / name
        if candidate_path.is_file():
            rel = name
            out.append(
                HelperCandidate(
                    id=_stable_id(rel),
                    kind="other",
                    source_path=rel,
                    authority="repository",
                )
            )
    return out


def _scan_native_build(repo_root: Path) -> list[HelperCandidate]:
    out: list[HelperCandidate] = []
    for name, kind in _NATIVE_BUILD.items():
        path = repo_root / name
        if path.is_file():
            out.append(
                HelperCandidate(
                    id=_stable_id(name),
                    kind=kind,
                    source_path=name,
                    authority="repository",
                )
            )
    for name in _TASKFILE_NAMES + _JUSTFILE_NAMES:
        path = repo_root / name
        if path.is_file():
            out.append(
                HelperCandidate(
                    id=_stable_id(name),
                    kind="build",
                    source_path=name,
                    authority="repository",
                )
            )
    return out


def _scan_ecosystem_manifests(repo_root: Path) -> list[HelperCandidate]:
    out: list[HelperCandidate] = []
    for name, kind in _ECOSYSTEM_MANIFESTS.items():
        path = repo_root / name
        if path.is_file():
            out.append(
                HelperCandidate(
                    id=_stable_id(name),
                    kind=kind,
                    source_path=name,
                    authority="ecosystem",
                )
            )
    return out


def _scan_deploy_files(repo_root: Path) -> list[HelperCandidate]:
    out: list[HelperCandidate] = []
    for name, kind in _DEPLOY_FILES.items():
        path = repo_root / name
        if path.is_file():
            out.append(
                HelperCandidate(
                    id=_stable_id(name),
                    kind=kind,
                    source_path=name,
                    authority="repository",
                )
            )
    return out


def _scan_ci_workflows(repo_root: Path) -> list[HelperCandidate]:
    out: list[HelperCandidate] = []
    for ci_dir_name in _CI_DIRS:
        ci_dir = repo_root / ci_dir_name
        if not ci_dir.is_dir():
            continue
        for entry in sorted(ci_dir.rglob("*")):
            if not entry.is_file():
                continue
            if entry.suffix not in _CI_FILE_SUFFIXES:
                continue
            rel = _repo_rel(repo_root, entry)
            out.append(
                HelperCandidate(
                    id=_stable_id(rel),
                    kind="build",
                    source_path=rel,
                    authority="ci-used",
                )
            )
    return out


def _scan_infra(repo_root: Path) -> list[HelperCandidate]:
    """Helm charts, Ansible playbooks, Terraform stacks."""
    out: list[HelperCandidate] = []
    # Helm: any Chart.yaml anywhere in the tree.
    for chart in sorted(repo_root.rglob("Chart.yaml")):
        rel = _repo_rel(repo_root, chart)
        out.append(
            HelperCandidate(
                id=_stable_id(rel),
                kind="deploy",
                source_path=rel,
                authority="repository",
            )
        )
    # Ansible: playbooks under ansible/ or roles/.
    for subdir in ("ansible", "playbooks"):
        ans_dir = repo_root / subdir
        if ans_dir.is_dir():
            for entry in sorted(ans_dir.rglob("*.yml")) + sorted(ans_dir.rglob("*.yaml")):
                if not entry.is_file():
                    continue
                rel = _repo_rel(repo_root, entry)
                out.append(
                    HelperCandidate(
                        id=_stable_id(rel),
                        kind="deploy",
                        source_path=rel,
                        authority="repository",
                    )
                )
    # Terraform: any *.tf file.
    for tf in sorted(repo_root.rglob("*.tf")):
        # Skip vendored terraform under .terraform/
        rel = _repo_rel(repo_root, tf)
        if "/.terraform/" in rel or rel.startswith(".terraform/"):
            continue
        out.append(
            HelperCandidate(
                id=_stable_id(rel),
                kind="deploy",
                source_path=rel,
                authority="repository",
            )
        )
    return out


def discover_helpers(repo_root: str | Path) -> list[HelperCandidate]:
    """Scan ``repo_root`` for project-native helpers (spec §4.3).

    Returns candidates in spec priority order: repository instructions first,
    then native entry points, CI workflows, ecosystem manifests, deploy files,
    and infrastructure. Within a category the order is deterministic (sorted
    by path).

    Raises:
        FileNotFoundError: ``repo_root`` does not exist.
        NotADirectoryError: ``repo_root`` is not a directory.
    """
    root = Path(repo_root)
    if not root.exists():
        raise FileNotFoundError(f"repository path does not exist: {repo_root}")
    if not root.is_dir():
        raise NotADirectoryError(f"repository path is not a directory: {repo_root}")
    return [
        *_scan_repo_docs(root),
        *_scan_native_build(root),
        *_scan_ci_workflows(root),
        *_scan_ecosystem_manifests(root),
        *_scan_deploy_files(root),
        *_scan_infra(root),
    ]
