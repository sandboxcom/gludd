#!/usr/bin/env python3
"""Gate new staged or committed clones through the locked jscpd engine."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING or __package__:
    from scripts.staged_snapshot import (
        SnapshotError,
        discover_changed_paths,
        discover_staged_paths,
        materialize_index,
        materialize_ref,
        read_index_blob,
        read_ref_blob,
    )
else:
    from staged_snapshot import (
        SnapshotError,
        discover_changed_paths,
        discover_staged_paths,
        materialize_index,
        materialize_ref,
        read_index_blob,
        read_ref_blob,
    )

ROOT = Path(__file__).resolve().parent.parent
Source = Literal["staged", "committed"]
POLICY_KEYS = frozenset(
    {
        "path",
        "minLines",
        "minTokens",
        "mode",
        "ignoreIdentifiers",
        "ignoreLiterals",
        "maxSize",
        "reporters",
        "ignore",
        "noTips",
    }
)
ENGINE_WORKERS = 2
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_REPORT_CLONES = 20_000
MAX_NEW_CLONE_DETAILS = 100
CONTROL_PATHS = frozenset(
    {
        ".opencode/package-lock.json",
        ".opencode/package.json",
        ".pre-commit-config.yaml",
        "make/00-foundation.mk",
        "make/10-observability-and-tests.mk",
        "make/20-recovery-and-git.mk",
        "make/40-cross-version-and-worktrees.mk",
        "scripts/check_duplicate_code.py",
        "scripts/staged_snapshot.py",
    }
)


class DuplicateCodeError(RuntimeError):
    """Raised when clone admission cannot prove its inputs or execute safely."""


@dataclass(frozen=True, slots=True)
class DuplicatePolicy:
    """Validated bounded settings consumed by the mature detector."""

    production_paths: tuple[str, ...]
    min_lines: int
    min_tokens: int
    max_size: str
    workers: int
    ignore_identifiers: bool
    ignore_literals: bool


def _safe_path(raw: object, *, source: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise DuplicateCodeError(f"{source} must be a non-empty repository path")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or str(path) != raw:
        raise DuplicateCodeError(f"{source} is not normalized: {raw!r}")
    return raw.rstrip("/")


def load_policy(data: bytes) -> DuplicatePolicy:
    """Validate the exact jscpd policy that bounds pre-commit work."""

    try:
        payload: Any = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DuplicateCodeError(f"duplicate-code policy is not UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DuplicateCodeError("duplicate-code policy root must be an object")
    actual_keys = frozenset(payload)
    if actual_keys != POLICY_KEYS:
        raise DuplicateCodeError(
            "duplicate-code policy keys drifted: "
            f"missing={sorted(POLICY_KEYS - actual_keys)} "
            f"unexpected={sorted(actual_keys - POLICY_KEYS)}"
        )
    raw_paths = payload["path"]
    if not isinstance(raw_paths, list) or not raw_paths:
        raise DuplicateCodeError("duplicate-code path must be a non-empty list")
    paths = tuple(
        sorted(_safe_path(item, source=f"path[{index}]") for index, item in enumerate(raw_paths))
    )
    if len(paths) != len(set(paths)):
        raise DuplicateCodeError("duplicate-code production paths must be unique")
    min_lines = payload["minLines"]
    min_tokens = payload["minTokens"]
    if isinstance(min_lines, bool) or not isinstance(min_lines, int) or min_lines < 12:
        raise DuplicateCodeError("minLines must be an integer of at least 12")
    if isinstance(min_tokens, bool) or not isinstance(min_tokens, int) or min_tokens < 80:
        raise DuplicateCodeError("minTokens must be an integer of at least 80")
    if payload["mode"] != "weak":
        raise DuplicateCodeError("mode must remain weak so comments do not create clones")
    if payload["maxSize"] != "500kb":
        raise DuplicateCodeError("maxSize must remain exactly 500kb")
    if payload["reporters"] != ["json", "silent"]:
        raise DuplicateCodeError("reporters must remain json,silent for bounded output")
    if payload["ignoreIdentifiers"] is not True or payload["ignoreLiterals"] is not True:
        raise DuplicateCodeError("renamed clone detection must remain enabled")
    if payload["noTips"] is not True:
        raise DuplicateCodeError("noTips must remain true for deterministic output")
    ignore = payload["ignore"]
    if not isinstance(ignore, list) or any(
        not isinstance(item, str) or not item for item in ignore
    ):
        raise DuplicateCodeError("ignore must be a list of non-empty glob strings")
    return DuplicatePolicy(
        production_paths=paths,
        min_lines=min_lines,
        min_tokens=min_tokens,
        max_size=payload["maxSize"],
        workers=ENGINE_WORKERS,
        ignore_identifiers=True,
        ignore_literals=True,
    )


def _repository_relative(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise DuplicateCodeError(f"config path is outside repository: {path}") from exc
    return _safe_path(relative, source="config path")


def _config_bytes(
    root: Path,
    config_relative: str,
    *,
    source: Source,
    current_ref: str,
) -> bytes:
    try:
        if source == "staged":
            blob = read_index_blob(root, config_relative)
            if blob.mode == "160000":
                raise DuplicateCodeError("duplicate-code config cannot be a Git submodule")
            return blob.data
        return read_ref_blob(root, current_ref, config_relative)
    except SnapshotError as exc:
        raise DuplicateCodeError(str(exc)) from exc


def _changed_paths(
    root: Path,
    *,
    source: Source,
    base_ref: str,
    current_ref: str,
) -> tuple[str, ...]:
    try:
        if source == "staged":
            return discover_staged_paths(root)
        return discover_changed_paths(root, base_ref, current_ref)
    except SnapshotError as exc:
        raise DuplicateCodeError(str(exc)) from exc


def _needs_scan(
    changed_paths: tuple[str, ...],
    production_paths: tuple[str, ...],
    config_relative: str,
) -> bool:
    controls = CONTROL_PATHS | {config_relative}
    for changed in changed_paths:
        if changed in controls:
            return True
        if any(changed == root or changed.startswith(f"{root}/") for root in production_paths):
            return True
    return False


def _run_engine(
    engine: Path,
    *,
    cwd: Path,
    baseline: Path,
    update: bool,
    report_dir: Path | None = None,
) -> int:
    command = [
        str(engine),
        "--config",
        ".jscpd.json",
        "--baseline",
        str(baseline),
        "--update-baseline" if update else "--fail-on-new-clones",
        "--workers",
        str(ENGINE_WORKERS),
        "--reporters",
        "silent" if update else "json,silent",
    ]
    if not update:
        if report_dir is None:
            raise DuplicateCodeError("candidate scan requires an explicit report directory")
        command.extend(("--output", str(report_dir)))
    phase = "baseline" if update else "candidate"
    print(f"duplicate-code: phase={phase} engine=jscpd source={cwd}", flush=True)
    try:
        result = subprocess.run(command, cwd=cwd, check=False)
    except OSError as exc:
        raise DuplicateCodeError(f"cannot execute locked jscpd engine: {exc}") from exc
    if result.returncode != 0:
        print(
            f"duplicate-code: FAIL phase={phase} exit={result.returncode}",
            file=sys.stderr,
        )
    return result.returncode


def _report_integer(payload: dict[str, Any], key: str, *, source: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DuplicateCodeError(f"{source}.{key} must be a non-negative integer")
    return value


def _report_location(payload: object, candidate: Path, *, source: str) -> str:
    if not isinstance(payload, dict):
        raise DuplicateCodeError(f"{source} must be an object")
    raw_name = payload.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise DuplicateCodeError(f"{source}.name must be a non-empty path")
    path = Path(raw_name)
    if path.is_absolute():
        try:
            relative = path.resolve().relative_to(candidate.resolve()).as_posix()
        except ValueError as exc:
            raise DuplicateCodeError(
                f"{source}.name is outside the candidate snapshot: {raw_name}"
            ) from exc
    else:
        relative = _safe_path(PurePosixPath(raw_name).as_posix(), source=f"{source}.name")
    start = _report_integer(payload, "start", source=source)
    end = _report_integer(payload, "end", source=source)
    if start < 1 or end < start:
        raise DuplicateCodeError(f"{source} has an invalid line range {start}-{end}")
    return f"{relative}:{start}-{end}"


def emit_report(report_path: Path, candidate: Path) -> int:
    """Validate and emit a bounded summary plus locations for new clones."""

    try:
        size = report_path.stat().st_size
    except OSError as exc:
        raise DuplicateCodeError(f"jscpd JSON report is unavailable: {exc}") from exc
    if size <= 0 or size > MAX_REPORT_BYTES:
        raise DuplicateCodeError(
            f"jscpd JSON report size must be 1..{MAX_REPORT_BYTES} bytes; got {size}"
        )
    try:
        payload: Any = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DuplicateCodeError(f"jscpd JSON report is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise DuplicateCodeError("jscpd JSON report root must be an object")
    duplicates = payload.get("duplicates")
    statistics = payload.get("statistics")
    if not isinstance(duplicates, list):
        raise DuplicateCodeError("jscpd JSON report duplicates must be a list")
    if len(duplicates) > MAX_REPORT_CLONES:
        raise DuplicateCodeError(
            f"jscpd JSON report exceeds the {MAX_REPORT_CLONES}-clone safety bound"
        )
    if not isinstance(statistics, dict) or not isinstance(statistics.get("total"), dict):
        raise DuplicateCodeError("jscpd JSON report statistics.total must be an object")
    total = statistics["total"]
    clone_count = _report_integer(total, "clones", source="statistics.total")
    new_count = _report_integer(total, "newClones", source="statistics.total")
    duplicated_lines = _report_integer(
        total, "duplicatedLines", source="statistics.total"
    )
    new_duplicated_lines = _report_integer(
        total, "newDuplicatedLines", source="statistics.total"
    )
    if clone_count != len(duplicates) or new_count > clone_count:
        raise DuplicateCodeError(
            "jscpd JSON clone totals do not match the duplicate records"
        )
    if new_duplicated_lines > duplicated_lines:
        raise DuplicateCodeError("jscpd JSON new duplicated lines exceed total lines")

    new_clones: list[dict[str, Any]] = []
    for index, duplicate in enumerate(duplicates):
        if not isinstance(duplicate, dict) or not isinstance(duplicate.get("isNew"), bool):
            raise DuplicateCodeError(f"duplicates[{index}].isNew must be a boolean")
        if duplicate["isNew"]:
            new_clones.append(duplicate)
    if len(new_clones) != new_count:
        raise DuplicateCodeError("jscpd JSON new clone total does not match isNew records")

    print(
        "duplicate-code: report "
        f"clones={clone_count} existing={clone_count - new_count} new={new_count} "
        f"duplicated_lines={duplicated_lines} "
        f"new_duplicated_lines={new_duplicated_lines}"
    )
    for index, duplicate in enumerate(new_clones[:MAX_NEW_CLONE_DETAILS], start=1):
        clone_format = duplicate.get("format")
        kind = duplicate.get("kind")
        if not isinstance(clone_format, str) or not clone_format:
            raise DuplicateCodeError(f"new clone {index} has no format")
        if not isinstance(kind, str) or not kind:
            raise DuplicateCodeError(f"new clone {index} has no kind")
        lines = _report_integer(duplicate, "lines", source=f"new clone {index}")
        tokens = _report_integer(duplicate, "tokens", source=f"new clone {index}")
        first = _report_location(
            duplicate.get("firstFile"), candidate, source=f"new clone {index}.firstFile"
        )
        second = _report_location(
            duplicate.get("secondFile"), candidate, source=f"new clone {index}.secondFile"
        )
        print(
            f"duplicate-code: NEW {index}/{new_count} format={clone_format} kind={kind} "
            f"lines={lines} tokens={tokens} first={first} second={second}"
        )
    if new_count > MAX_NEW_CLONE_DETAILS:
        print(
            "duplicate-code: NEW details_truncated="
            f"{new_count - MAX_NEW_CLONE_DETAILS} safety_limit={MAX_NEW_CLONE_DETAILS}"
        )
    return new_count


def run_guard(
    *,
    root: Path,
    config_path: Path,
    engine: Path,
    source: Source,
    base_ref: str,
    current_ref: str,
) -> int:
    """Compare a staged/committed production snapshot with its explicit base."""

    config_relative = _repository_relative(root, config_path)
    config_data = _config_bytes(
        root,
        config_relative,
        source=source,
        current_ref=current_ref,
    )
    policy = load_policy(config_data)
    changed = _changed_paths(
        root,
        source=source,
        base_ref=base_ref,
        current_ref=current_ref,
    )
    if not _needs_scan(changed, policy.production_paths, config_relative):
        print(f"duplicate-code: SKIP changed_production=0 source={source}")
        return 0

    resolved_engine = engine if engine.is_absolute() else root / engine
    if not resolved_engine.is_file() or not os.access(resolved_engine, os.X_OK):
        raise DuplicateCodeError(
            f"locked jscpd engine is unavailable at {resolved_engine}; "
            "run make node-deps-sync with the documented variables"
        )

    with tempfile.TemporaryDirectory(prefix="gludd-duplicate-code-") as temporary:
        temporary_root = Path(temporary)
        base = temporary_root / "base"
        candidate = temporary_root / "candidate"
        base.mkdir()
        candidate.mkdir()
        try:
            materialize_ref(root, base_ref, base, policy.production_paths)
            if source == "staged":
                materialize_index(root, candidate, policy.production_paths)
            else:
                materialize_ref(root, current_ref, candidate, policy.production_paths)
        except SnapshotError as exc:
            raise DuplicateCodeError(str(exc)) from exc
        (base / ".jscpd.json").write_bytes(config_data)
        (candidate / ".jscpd.json").write_bytes(config_data)
        baseline = temporary_root / "baseline.json"
        result = _run_engine(resolved_engine, cwd=base, baseline=baseline, update=True)
        if result != 0:
            return result
        if not baseline.is_file() or baseline.stat().st_size == 0:
            raise DuplicateCodeError("jscpd did not create the requested baseline")
        report_dir = temporary_root / "report"
        result = _run_engine(
            resolved_engine,
            cwd=candidate,
            baseline=baseline,
            update=False,
            report_dir=report_dir,
        )
        new_clones = emit_report(report_dir / "jscpd-report.json", candidate)
        if result == 0 and new_clones:
            raise DuplicateCodeError(
                "jscpd reported new clones but returned success instead of blocking"
            )
        if result == 0:
            print(
                f"duplicate-code: PASS source={source} changed={len(changed)} "
                f"base={base_ref} current={current_ref}"
            )
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail when locked jscpd finds clones new to a staged/committed snapshot."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--source", choices=("staged", "committed"), required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--current-ref", required=True)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.validate_only:
            load_policy(args.config.read_bytes())
            print(
                "duplicate-code: VALIDATED "
                f"source={args.source} base={args.base_ref} current={args.current_ref}"
            )
            return 0
        return run_guard(
            root=args.root,
            config_path=args.config,
            engine=args.engine,
            source=args.source,
            base_ref=args.base_ref,
            current_ref=args.current_ref,
        )
    except (DuplicateCodeError, OSError) as exc:
        print(f"duplicate-code: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
