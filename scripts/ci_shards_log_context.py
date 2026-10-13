#!/usr/bin/env python3
"""Extract bounded context from local or exact-run CI shard logs."""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

_SAFE_ARTIFACT_FILE = re.compile(r"[A-Za-z0-9._-]+")
# Hard UTF-8 display limits include each newline printed by main(). The
# input file is untouched; only its displayed context is clipped.
_MAX_LINE_BYTES = 2_048
_MAX_OUTPUT_BYTES = 32_768
_LINE_TRUNCATION_NOTICE = " ... [line truncated; source unmodified]"


def _bounded_line(line: str, *, prefix: str = "", pattern: str | None = None) -> str:
    """Clip whole UTF-8 characters, keeping a late literal hit when it fits."""
    rendered = prefix + line
    encoded = rendered.encode("utf-8")
    if len(encoded) + 1 <= _MAX_LINE_BYTES:
        return rendered
    content_bytes = _MAX_LINE_BYTES - 1 - len(_LINE_TRUNCATION_NOTICE.encode("utf-8"))
    if pattern is not None:
        match_start = line.find(pattern)
        match_end = match_start + len(pattern)
        match_end_bytes = len((prefix + line[:match_end]).encode("utf-8"))
        if match_start > 0 and match_end_bytes > content_bytes:
            rendered = prefix + "[line truncated before match] " + line[match_start:]
            encoded = rendered.encode("utf-8")
    content = encoded[:content_bytes].decode("utf-8", errors="ignore")
    return content + _LINE_TRUNCATION_NOTICE


def _bounded_output(lines: Iterable[str]) -> list[str]:
    """Retain fitting output exactly and make room for a visible total-limit notice."""
    output: list[str] = []
    used_bytes = 0
    notice = (
        f"--- output truncated: {_MAX_OUTPUT_BYTES}-byte UTF-8 limit; "
        "source unmodified ---"
    )
    notice_bytes = len(notice.encode("utf-8")) + 1
    for raw_line in lines:
        line = _bounded_line(raw_line)
        line_bytes = len(line.encode("utf-8")) + 1
        if used_bytes + line_bytes > _MAX_OUTPUT_BYTES:
            while output and used_bytes + notice_bytes > _MAX_OUTPUT_BYTES:
                used_bytes -= len(output.pop().encode("utf-8")) + 1
            output.append(notice)
            break
        output.append(line)
        used_bytes += line_bytes
    return output


def _is_relative_to(path: Path, root: Path) -> bool:
    """Check containment without accepting sibling paths."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _allowed_log_path(path: Path, allowed_root: Path | None = None) -> bool:
    """Retain local-log and exact artifact-root input guards."""
    if ".." in path.parts:
        return False
    if allowed_root is None:
        return ".gate-logs" in path.parts
    if allowed_root.is_symlink() or not allowed_root.is_dir() or path.is_symlink():
        return False
    try:
        resolved_root = allowed_root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError:
        return False
    return _is_relative_to(resolved_path, resolved_root) and resolved_path.is_file()


def resolve_artifact_file(artifact_root: Path, file_name: str) -> Path:
    """Resolve exactly one safe basename inside an exact-run artifact root."""
    if Path(file_name).name != file_name or _SAFE_ARTIFACT_FILE.fullmatch(file_name) is None:
        raise ValueError(f"artifact file must be a safe basename: {file_name}")
    if artifact_root.is_symlink():
        raise ValueError(f"artifact root must not be a symlink: {artifact_root}")
    if not artifact_root.is_dir():
        raise FileNotFoundError(artifact_root)
    resolved_root = artifact_root.resolve(strict=True)
    matches: list[Path] = []
    for candidate in artifact_root.rglob(file_name):
        if candidate.is_symlink():
            raise ValueError(f"artifact file must not be a symlink: {candidate}")
        if not candidate.is_file():
            continue
        resolved = candidate.resolve(strict=True)
        if not _is_relative_to(resolved, resolved_root):
            raise ValueError(f"artifact file escaped its run-bound root: {candidate}")
        matches.append(resolved)
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one artifact file named {file_name}, found {len(matches)}"
        )
    return matches[0]


def extract_context(
    log_path: Path,
    pattern: str,
    *,
    before: int = 20,
    after: int = 80,
    max_matches: int = 5,
    allowed_root: Path | None = None,
) -> list[str]:
    """Return byte-bounded context around full source lines containing pattern."""
    if not pattern:
        raise ValueError("pattern is required")
    if before < 0 or after < 0 or max_matches < 1:
        raise ValueError("before/after must be >= 0 and max_matches must be >= 1")
    if not _allowed_log_path(log_path, allowed_root):
        if allowed_root is None:
            raise ValueError(f"refusing log path outside workspace gate logs: {log_path}")
        raise ValueError(f"refusing log path outside artifact root {allowed_root}: {log_path}")

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    matches = [idx for idx, line in enumerate(lines) if pattern in line]

    def context_lines() -> Iterator[str]:
        for match_number, idx in enumerate(matches[:max_matches], start=1):
            start = max(0, idx - before)
            end = min(len(lines), idx + after + 1)
            yield f"--- match {match_number}/{len(matches)} line {idx + 1} ---"
            for line_no in range(start, end):
                is_match = line_no == idx
                marker = ">" if is_match else " "
                yield _bounded_line(
                    lines[line_no],
                    prefix=f"{marker}{line_no + 1}: ",
                    pattern=pattern if is_match else None,
                )
        if not matches:
            yield f"No matches for pattern: {pattern}"

    return _bounded_output(context_lines())


def main() -> int:
    """Display bounded context while preserving success and refusal statuses."""
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--log", type=Path)
    source.add_argument("--artifact-root", type=Path)
    parser.add_argument("--artifact-file")
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--before", type=int, default=20)
    parser.add_argument("--after", type=int, default=80)
    parser.add_argument("--max-matches", type=int, default=5)
    args = parser.parse_args()

    try:
        if args.artifact_root is not None:
            if args.artifact_file is None:
                raise ValueError("--artifact-file is required with --artifact-root")
            log_path = resolve_artifact_file(args.artifact_root, args.artifact_file)
            allowed_root = args.artifact_root
        else:
            if args.artifact_file is not None:
                raise ValueError("--artifact-file requires --artifact-root")
            log_path = args.log
            allowed_root = None
        if log_path is None:
            raise ValueError("a log source is required")
        lines = extract_context(
            log_path,
            args.pattern,
            before=args.before,
            after=args.after,
            max_matches=args.max_matches,
            allowed_root=allowed_root,
        )
    except Exception as exc:
        print(_bounded_line(f"CI-SHARDS-LOG-CONTEXT error: {exc}"))
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
