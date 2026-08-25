#!/usr/bin/env python3
"""Enforce exact acquisition-to-teardown evidence for application resources.

Ruff's SIM115 and RUF006 remain the first line of defence for context-managed
files and referenced asyncio tasks.  This application-specific checker covers
the wider Gludd lifecycle: child processes, async tasks, network clients,
temporary artifacts, and explicitly managed services.  Every discovered
resource must have structural teardown evidence and an exact inventory entry;
new resources, removed/stale entries, and unowned acquisitions fail closed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_PROCESS_CALLS = frozenset({"subprocess.Popen", "Popen"})
_TASK_CALLS = frozenset({"asyncio.create_task", "create_task"})
_CLIENT_NAMES = frozenset(
    {
        "AsyncClient",
        "Client",
        "ClientSession",
        "Session",
        "TestClient",
    }
)
_TEMP_CALLS = frozenset(
    {
        "tempfile.NamedTemporaryFile",
        "tempfile.SpooledTemporaryFile",
        "tempfile.TemporaryDirectory",
        "tempfile.TemporaryFile",
        "tempfile.mkdtemp",
        "tempfile.mkstemp",
    }
)
_SERVICE_NAMES = frozenset(
    {
        "EndpointLifecycle",
        "LocalInferenceManager",
        "ProcessBackend",
        "SearxServer",
        "WriterProcess",
    }
)
_TEARDOWN_METHODS = {
    "process": frozenset({"communicate", "kill", "terminate", "wait"}),
    "async-task": frozenset({"cancel"}),
    "client": frozenset({"aclose", "close", "disconnect", "loop_stop"}),
    "temp-artifact": frozenset({"cleanup", "close", "unlink"}),
    "service": frozenset({"aclose", "close", "shutdown", "stop", "stop_all", "stop_server"}),
}
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_SKIP_PARTS = frozenset({".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__"})


@dataclass(frozen=True, order=True)
class ResourceEvidence:
    """One exact resource acquisition and its structural teardown evidence."""

    path: str
    line: int
    column: int
    kind: str
    owner: str
    acquisition: str
    teardown: str
    source_hash: str
    owned: bool

    def key(self) -> tuple[str, int, int, str, str]:
        """Return the immutable inventory identity for this acquisition."""
        return (self.path, self.line, self.column, self.kind, self.source_hash)

    def as_dict(self) -> dict[str, object]:
        """Render stable JSON-compatible evidence."""
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "kind": self.kind,
            "owner": self.owner,
            "acquisition": self.acquisition,
            "teardown": self.teardown,
            "source_hash": self.source_hash,
            "owned": self.owned,
        }


def _dotted_name(node: ast.AST) -> str:
    """Return a dotted name for a call target when statically knowable."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _classify(call: ast.Call) -> str | None:
    name = _dotted_name(call.func)
    final = name.rsplit(".", 1)[-1]
    if name in _PROCESS_CALLS:
        return "process"
    if name in _TASK_CALLS or name.endswith(".create_task"):
        return "async-task"
    if name in _TEMP_CALLS:
        return "temp-artifact"
    if final in _CLIENT_NAMES:
        return "client"
    if final in _SERVICE_NAMES:
        return "service"
    return None


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _scope_for(node: ast.AST, parents: dict[ast.AST, ast.AST], tree: ast.Module) -> ast.AST:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.AsyncFunctionDef, ast.FunctionDef, ast.Lambda)):
            return current
    return tree


def _class_for(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.ClassDef | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.ClassDef):
            return current
    return None


def _owner_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    names: list[str] = []
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)):
            names.append(current.name)
    return ".".join(reversed(names)) or "<module>"


def _assigned_target(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> str:
    current: ast.AST = call
    while current in parents:
        parent = parents[current]
        if isinstance(parent, (ast.Assign, ast.AnnAssign)):
            target = parent.targets[0] if isinstance(parent, ast.Assign) else parent.target
            if isinstance(target, (ast.Attribute, ast.Name)):
                return ast.unparse(target)
            if isinstance(target, (ast.List, ast.Tuple)):
                named = [item for item in target.elts if isinstance(item, (ast.Attribute, ast.Name))]
                return ast.unparse(named[-1]) if named else ""
            return ""
        if isinstance(parent, (ast.Expr, ast.Return, ast.With, ast.AsyncWith)):
            break
        current = parent
    return ""


def _direct_context_manager(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    current: ast.AST = call
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.withitem):
            return parent.context_expr is current
        if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)):
            return False
        current = parent
    return False


def _task_group_owner(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    receiver = _dotted_name(call.func).rsplit(".", 1)[0]
    if not receiver:
        return False
    current: ast.AST = call
    while current in parents:
        current = parents[current]
        if not isinstance(current, ast.AsyncWith):
            continue
        for item in current.items:
            if not isinstance(item.context_expr, ast.Call):
                continue
            if _dotted_name(item.context_expr.func) not in {"TaskGroup", "asyncio.TaskGroup"}:
                continue
            if item.optional_vars is not None and ast.unparse(item.optional_vars) == receiver:
                return True
    return False


def _is_awaited(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    current: ast.AST = call
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Await):
            return True
        if isinstance(current, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)):
            return False
    return False


def _is_in_finally(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    child = node
    while child in parents:
        parent = parents[child]
        if isinstance(parent, ast.Try) and child in parent.finalbody:
            return True
        child = parent
    return False


def _contains_target(node: ast.AST, target: str) -> bool:
    return any(ast.unparse(item) == target for item in ast.walk(node))


def _returned(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    current: ast.AST = call
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Return):
            return True
        if isinstance(current, (ast.Assign, ast.AnnAssign, ast.Expr)):
            return False
    return False


def _target_aliases(target: str, search_scope: ast.AST) -> set[str]:
    aliases = {target}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(search_scope):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            assigned = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if not isinstance(assigned, (ast.Attribute, ast.Name)):
                continue
            assigned_text = ast.unparse(assigned)
            value = node.value
            wrapper_call = isinstance(value, ast.Call) and _dotted_name(value.func).rsplit(".", 1)[-1] in {
                "Path",
                "_secure_directory",
                "cls",
                "str",
                "tuple",
            }
            flows_from_alias = isinstance(value, (ast.Attribute, ast.BinOp, ast.Name)) or wrapper_call
            flows_from_alias = value is not None and flows_from_alias and any(
                _contains_target(value, alias) for alias in aliases
            )
            if isinstance(value, ast.Call) and _dotted_name(value.func) == "getattr":
                attributes = {alias.rsplit(".", 1)[-1] for alias in aliases}
                flows_from_alias = (
                    len(value.args) >= 2
                    and isinstance(value.args[1], ast.Constant)
                    and value.args[1].value in attributes
                )
            if flows_from_alias and assigned_text not in aliases:
                aliases.add(assigned_text)
                changed = True
    return aliases


def _container_cleanup(
    target: str,
    kind: str,
    search_scope: ast.AST,
) -> list[str]:
    evidence: list[str] = []
    aliases = _target_aliases(target, search_scope)
    for node in ast.walk(search_scope):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            if isinstance(node, ast.Call) and kind == "async-task":
                final = _dotted_name(node.func).rsplit(".", 1)[-1].lower()
                if ("drain" in final or "cancel" in final) and any(
                    any(_contains_target(argument, alias) for alias in aliases)
                    for argument in node.args
                ):
                    evidence.append(ast.unparse(node))
            continue
        if kind == "async-task":
            final = node.func.attr.lower()
            if final in {"gather", "wait"} and any(
                any(_contains_target(argument, alias) for alias in aliases)
                for argument in node.args
            ):
                evidence.append(f"{ast.unparse(node)}")
            elif ("drain" in final or "cancel" in final) and any(
                any(_contains_target(argument, alias) for alias in aliases)
                for argument in node.args
            ):
                evidence.append(ast.unparse(node))
    for node in ast.walk(search_scope):
        if not isinstance(node, (ast.For, ast.AsyncFor)) or not any(
            _contains_target(node.iter, alias) for alias in aliases
        ):
            continue
        alias = ast.unparse(node.target)
        for child in ast.walk(node):
            if isinstance(child, ast.Await) and _contains_target(child.value, alias):
                evidence.append(f"for {alias} in {target}: await {alias}")
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if ast.unparse(child.func.value) == alias and child.func.attr in _TEARDOWN_METHODS[kind]:
                evidence.append(f"for {alias} in {target}: {ast.unparse(child)}")
            if kind == "temp-artifact" and child.func.attr in {"replace", "rmtree", "unlink"} and any(
                _contains_target(argument, alias) for argument in child.args
            ):
                evidence.append(f"for {alias} in {target}: {ast.unparse(child)}")
    return evidence


def _atomic_temp_teardown(aliases: set[str], search_scope: ast.AST) -> bool:
    """Recognize atomic rename success paired with exception-path unlink."""
    for node in ast.walk(search_scope):
        if not isinstance(node, ast.Try):
            continue
        replaced = any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "replace"
            and bool(child.args)
            and any(_contains_target(child.args[0], alias) for alias in aliases)
            for statement in node.body
            for child in ast.walk(statement)
        )
        unlinked = any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "unlink"
            and any(
                any(_contains_target(argument, alias) for alias in aliases)
                for argument in child.args
            )
            for handler in node.handlers
            for statement in handler.body
            for child in ast.walk(statement)
        )
        if replaced and unlinked:
            return True
    return False


def _transfer_evidence(
    call: ast.Call,
    kind: str,
    target: str,
    scope: ast.AST,
    class_scope: ast.ClassDef | None,
    module_scope: ast.Module,
    parents: dict[ast.AST, ast.AST],
) -> list[str]:
    evidence: list[str] = []
    if _returned(call, parents) and not target.startswith("self."):
        evidence.append("ownership-transfer:return")
    if not target:
        current: ast.AST = call
        while current in parents:
            current = parents[current]
            if isinstance(current, ast.Call):
                outer = _dotted_name(current.func).rsplit(".", 1)[-1].lower()
                if "track" in outer:
                    evidence.append(f"ownership-transfer:{ast.unparse(current)}")
                    break
            if isinstance(current, (ast.Expr, ast.Return)):
                break
        return evidence
    search_scope = class_scope or scope
    aliases = _target_aliases(target, search_scope)
    for node in ast.walk(scope):
        if (
            not target.startswith("self.")
            and isinstance(node, ast.Return)
            and node.value is not None
            and any(_contains_target(node, alias) for alias in aliases)
        ):
            evidence.append(f"ownership-transfer:return {ast.unparse(node.value)}")
    containers: set[str] = set()
    for node in ast.walk(search_scope):
        if not isinstance(node, ast.Call):
            continue
        call_name = _dotted_name(node.func)
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"add", "append"} and any(
            any(_contains_target(argument, alias) for alias in aliases) for argument in node.args
        ):
            containers.add(ast.unparse(node.func.value))
            continue
        if not any(
            any(_contains_target(argument, alias) for alias in aliases)
            for argument in node.args
        ):
            continue
        final = call_name.rsplit(".", 1)[-1].lower()
        if any(token in final for token in ("cleanup", "drain", "kill", "reap", "stop", "terminate", "track")):
            evidence.append(f"ownership-transfer:{ast.unparse(node)}")
        if kind == "process" and "timeout" in final:
            evidence.append(f"bounded-process-owner:{ast.unparse(node)}")
    for container in sorted(containers):
        cleanup_scope = (
            module_scope
            if isinstance(scope, (ast.AsyncFunctionDef, ast.FunctionDef))
            and "." not in container
            else search_scope
        )
        cleanup = _container_cleanup(container, kind, cleanup_scope)
        if cleanup:
            evidence.append(f"ownership-transfer:{container}; {'; '.join(cleanup)}")
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call) or not _is_in_finally(node, parents):
                continue
            final = _dotted_name(node.func).rsplit(".", 1)[-1].lower()
            if not any(token in final for token in ("cleanup", "drain", "kill", "reap", "stop", "terminate")):
                continue
            if any(_contains_target(argument, container) for argument in node.args):
                evidence.append(f"ownership-transfer:{ast.unparse(node)}")
    if kind == "process":
        starts_detached = any(
            keyword.arg == "start_new_session"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in call.keywords
        )
        pid_is_persisted = any(
            isinstance(node, ast.Attribute)
            and node.attr == "pid"
            and ast.unparse(node.value) == target
            for node in ast.walk(scope)
        )
        if starts_detached and pid_is_persisted:
            evidence.append("ownership-transfer:persisted-detached-pid")
    return evidence


def _deferred_lifespan_cleanup(node: ast.AST, scope: ast.AST) -> bool:
    """Return whether an async-contextmanager defers body failure past cleanup."""
    if not isinstance(scope, ast.AsyncFunctionDef):
        return False
    decorators = {_dotted_name(item) for item in scope.decorator_list}
    if not any(name.endswith("asynccontextmanager") for name in decorators):
        return False
    failure_names: set[str] = set()
    yield_end = 0
    for candidate in ast.walk(scope):
        if not isinstance(candidate, ast.Try) or not any(
            isinstance(child, (ast.Yield, ast.YieldFrom)) for child in ast.walk(candidate)
        ):
            continue
        yield_end = max(yield_end, candidate.end_lineno or candidate.lineno)
        for handler in candidate.handlers:
            if handler.type is None or _dotted_name(handler.type) not in {
                "BaseException",
                "Exception",
            }:
                continue
            for child in ast.walk(handler):
                if isinstance(child, ast.Assign) and isinstance(child.targets[0], ast.Name):
                    failure_names.add(child.targets[0].id)
    reraises = any(
        isinstance(candidate, ast.Raise)
        and candidate.exc is not None
        and any(_contains_target(candidate.exc, name) for name in failure_names)
        for candidate in ast.walk(scope)
    )
    return bool(failure_names and reraises and getattr(node, "lineno", 0) > yield_end)


def _candidate_teardowns(
    call: ast.Call,
    kind: str,
    target: str,
    scope: ast.AST,
    class_scope: ast.ClassDef | None,
    module_scope: ast.Module,
    parents: dict[ast.AST, ast.AST],
) -> list[str]:
    if not target:
        return _transfer_evidence(call, kind, target, scope, class_scope, module_scope, parents)
    search_scope = class_scope if target.startswith("self.") and class_scope is not None else scope
    evidence: list[str] = []
    aliases = _target_aliases(target, search_scope)
    if class_scope is not None and any(alias.startswith("self.") for alias in aliases):
        search_scope = class_scope
        aliases = _target_aliases(target, search_scope)
    if any(".state." in alias for alias in aliases):
        search_scope = module_scope
        aliases = _target_aliases(target, search_scope)
    for node in ast.walk(search_scope):
        if isinstance(node, ast.Await) and kind == "async-task" and any(
            _contains_target(node.value, alias) for alias in aliases
        ):
            evidence.append(f"await {ast.unparse(node.value)}")
            continue
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        receiver = ast.unparse(node.func.value)
        direct = any(
            (receiver == alias or receiver.startswith(f"{alias}."))
            and method in _TEARDOWN_METHODS[kind]
            for alias in aliases
        )
        aggregate_task = kind == "async-task" and method in {"gather", "wait"} and any(
            any(_contains_target(argument, alias) for alias in aliases) for argument in node.args
        )
        threaded_teardown = (
            method == "to_thread"
            and bool(node.args)
            and isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr in _TEARDOWN_METHODS[kind]
            and any(
                ast.unparse(node.args[0].value) == alias
                for alias in aliases
            )
        )
        temp_path_cleanup = kind == "temp-artifact" and method in {"replace", "rmtree", "unlink"} and any(
            any(_contains_target(argument, alias) for alias in aliases) for argument in node.args
        )
        if not (direct or aggregate_task or temp_path_cleanup or threaded_teardown):
            continue
        cross_method = class_scope is not None and any(alias.startswith("self.") for alias in aliases)
        completion = kind == "async-task" and method in {"gather", "wait"}
        if _is_in_finally(node, parents) or cross_method or completion or _deferred_lifespan_cleanup(node, scope):
            evidence.append(ast.unparse(node))
    for alias in aliases:
        evidence.extend(_container_cleanup(alias, kind, search_scope))
    if kind == "temp-artifact" and _atomic_temp_teardown(aliases, search_scope):
        evidence.append("atomic-replace-or-unlink")
    evidence.extend(
        _transfer_evidence(call, kind, target, scope, class_scope, module_scope, parents)
    )
    if kind == "service":
        state_roots = {
            alias.split(".state.", 1)[0]
            for alias in aliases
            if ".state." in alias
        }
        for node in ast.walk(module_scope):
            if not isinstance(node, ast.Call) or "lifespan" not in _dotted_name(node.func).lower():
                continue
            if any(
                any(_contains_target(argument, root) for argument in node.args)
                for root in state_roots
            ):
                evidence.append(f"lifespan-registration:{ast.unparse(node)}")
    return sorted(set(evidence))


def _evidence_for_call(
    call: ast.Call,
    *,
    kind: str,
    relative: str,
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
) -> ResourceEvidence:
    acquisition = ast.unparse(call)
    target = _assigned_target(call, parents)
    if _direct_context_manager(call, parents):
        teardown = "context-manager-exit"
    elif kind == "async-task" and _task_group_owner(call, parents):
        teardown = "TaskGroup.__aexit__"
    elif kind == "async-task" and _is_awaited(call, parents):
        teardown = "await task completion"
    else:
        candidates = _candidate_teardowns(
            call,
            kind,
            target,
            _scope_for(call, parents, tree),
            _class_for(call, parents),
            tree,
            parents,
        )
        teardown = "; ".join(candidates)
    owner = _owner_name(call, parents)
    digest_payload = "\0".join((kind, owner, acquisition, teardown))
    return ResourceEvidence(
        path=relative,
        line=call.lineno,
        column=call.col_offset,
        kind=kind,
        owner=owner,
        acquisition=acquisition,
        teardown=teardown,
        source_hash=hashlib.sha256(digest_payload.encode("utf-8")).hexdigest(),
        owned=bool(teardown),
    )


def scan_file(path: Path, *, root: Path) -> list[ResourceEvidence]:
    """Return exact lifecycle evidence for supported acquisitions in one file."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    parents = _build_parent_map(tree)
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    findings: list[ResourceEvidence] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _classify(node)
        if kind is not None:
            findings.append(
                _evidence_for_call(
                    node,
                    kind=kind,
                    relative=relative,
                    tree=tree,
                    parents=parents,
                )
            )
    return sorted(findings)


def _python_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in sorted(paths):
        if path.is_file() and path.suffix == ".py":
            yield path
            continue
        if not path.is_dir():
            raise FileNotFoundError(f"resource ownership scan path does not exist: {path}")
        for candidate in sorted(path.rglob("*.py")):
            if not any(part in _SKIP_PARTS for part in candidate.parts):
                yield candidate


def scan_paths(paths: Sequence[Path], *, root: Path) -> list[ResourceEvidence]:
    """Scan Python paths deterministically for owned application resources."""
    findings: list[ResourceEvidence] = []
    for path in _python_files(paths):
        findings.extend(scan_file(path, root=root))
    return sorted(findings)


def _evidence_from_item(item: object) -> ResourceEvidence:
    if not isinstance(item, dict):
        raise ValueError("inventory resources must be objects")
    evidence = ResourceEvidence(
        path=str(item.get("path", "")),
        line=int(item.get("line", 0)),
        column=int(item.get("column", -1)),
        kind=str(item.get("kind", "")),
        owner=str(item.get("owner", "")),
        acquisition=str(item.get("acquisition", "")),
        teardown=str(item.get("teardown", "")),
        source_hash=str(item.get("source_hash", "")),
        owned=item.get("owned") is True,
    )
    if (
        not evidence.path
        or evidence.line < 1
        or evidence.column < 0
        or evidence.kind not in _TEARDOWN_METHODS
        or not evidence.owner
        or not evidence.acquisition
        or not evidence.teardown
        or not evidence.owned
        or _HASH_RE.fullmatch(evidence.source_hash) is None
    ):
        raise ValueError(f"invalid or unowned inventory resource: {item!r}")
    return evidence


def load_inventory(path: Path) -> dict[tuple[str, int, int, str, str], ResourceEvidence]:
    """Load an exact, owned-only resource evidence inventory."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or not isinstance(raw.get("resources"), list):
        raise ValueError("inventory must use schema_version 1 with a resources list")
    result: dict[tuple[str, int, int, str, str], ResourceEvidence] = {}
    for item in raw["resources"]:
        evidence = _evidence_from_item(item)
        if evidence.key() in result:
            raise ValueError(f"duplicate inventory resource: {evidence.key()!r}")
        result[evidence.key()] = evidence
    return result


def validate_inventory(
    findings: Sequence[ResourceEvidence],
    inventory: dict[tuple[str, int, int, str, str], ResourceEvidence],
) -> list[str]:
    """Fail on unowned acquisitions or any exact inventory drift."""
    errors = [
        f"unowned resource: {item.path}:{item.line}:{item.column} [{item.kind}] {item.acquisition}"
        for item in findings
        if not item.owned
    ]
    actual = {item.key(): item for item in findings}
    for key in sorted(actual.keys() - inventory.keys()):
        item = actual[key]
        errors.append(f"new resource: {item.path}:{item.line}:{item.column} [{item.kind}] {item.source_hash}")
    for key in sorted(inventory.keys() - actual.keys()):
        item = inventory[key]
        errors.append(f"stale inventory: {item.path}:{item.line}:{item.column} [{item.kind}] {item.source_hash}")
    return errors


def write_inventory(path: Path, findings: Sequence[ResourceEvidence]) -> None:
    """Write exact evidence only when every acquisition is structurally owned."""
    unowned = [item for item in findings if not item.owned]
    if unowned:
        counts = {
            kind: sum(item.kind == kind for item in unowned)
            for kind in sorted(_TEARDOWN_METHODS)
            if any(item.kind == kind for item in unowned)
        }
        detail = " ".join(f"{kind}={count}" for kind, count in counts.items())
        examples = ", ".join(
            f"{item.path}:{item.line}[{item.kind}]" for item in unowned[:100]
        )
        raise ValueError(
            f"refusing to inventory {len(unowned)} unowned resource(s): {detail}; "
            f"first={examples}"
        )
    payload = {
        "schema_version": 1,
        "policy": "exact-path-line-column-kind-and-acquisition-teardown-sha256",
        "resources": [item.as_dict() for item in findings],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the resource-ownership checker CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--inventory", type=Path, default=Path("config/resource_ownership_inventory.json"))
    parser.add_argument("--write-inventory", action="store_true")
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("src/general_ludd"), Path("scripts")])
    args = parser.parse_args(argv)
    try:
        findings = scan_paths(args.paths, root=args.root)
        if args.write_inventory:
            write_inventory(args.inventory, findings)
            print(f"RESOURCE_OWNERSHIP_INVENTORY_WRITTEN resources={len(findings)} path={args.inventory}")
            return 0
        inventory = load_inventory(args.inventory)
    except (FileNotFoundError, UnicodeDecodeError, SyntaxError, ValueError, json.JSONDecodeError) as exc:
        print(f"RESOURCE_OWNERSHIP_FAIL error={exc}", file=sys.stderr)
        return 2
    errors = validate_inventory(findings, inventory)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    counts = {kind: sum(item.kind == kind for item in findings) for kind in sorted(_TEARDOWN_METHODS)}
    detail = " ".join(f"{kind}={count}" for kind, count in counts.items())
    print(f"RESOURCE_OWNERSHIP_PASS resources={len(findings)} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
