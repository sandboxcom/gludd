"""``gludd perm`` CLI subcommand — operator visibility into the permission system.

File-backed subcommands (list/show/grant/deny/revoke/edit/validate/diff/project)
read and write ``<config_dir>/permissions/<agent-type>.yml`` (with project
overrides at ``<config_dir>/permissions/projects/<project>/<agent-type>.yml``).

HTTP-backed subcommands (sts list/issue/inspect/revoke, audit) call the daemon
endpoints ``/admin/sts/*`` and ``/admin/sts/audit`` via httpx with PSK auth.

The parallel task that owns ``general_ludd.security.permissions`` and
``general_ludd.security.sts`` has not landed yet, so validation tries to import
``PermissionSpecParser`` from that module and falls back to a structural check
when it is absent. This keeps the CLI useful today and unchanged the day the
parser lands.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

PERM_SUBCOMMANDS = [
    "list",
    "show",
    "grant",
    "deny",
    "revoke",
    "edit",
    "validate",
    "diff",
    "project",
    "sts",
    "audit",
    "escalations",
]


# ---------------------------------------------------------------------------
# Spec store — file I/O for permission specs
# ---------------------------------------------------------------------------


class SpecStore:
    """Read/write PermissionSpec YAML files under a config directory."""

    def __init__(self, config_dir: str | Path) -> None:
        """Initialize the store rooted at ``<config_dir>/permissions``."""
        self.config_dir = Path(config_dir).expanduser()
        self.perms_dir = self.config_dir / "permissions"

    # ---- paths --------------------------------------------------------

    def spec_path(self, agent_type: str, project: str | None = None) -> Path:
        """Return the YAML path for an agent spec, optionally under a project override."""
        if project:
            return self.perms_dir / "projects" / project / f"{agent_type}.yml"
        return self.perms_dir / f"{agent_type}.yml"

    def all_spec_paths(self) -> list[Path]:
        """Return every top-level spec file under the permissions dir (sorted)."""
        if not self.perms_dir.is_dir():
            return []
        # only top-level *.yml (project overrides live in projects/<p>/)
        return sorted(p for p in self.perms_dir.glob("*.yml") if p.is_file())

    # ---- read ---------------------------------------------------------

    def load(self, agent_type: str, project: str | None = None) -> dict[str, Any] | None:
        """Load one spec as a dict, or None when the file is absent/invalid."""
        path = self.spec_path(agent_type, project)
        if not path.exists():
            return None
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            return None
        # ensure agent_type is set even if the file omits it
        data.setdefault("agent_type", agent_type)
        return data

    def load_all(self) -> list[dict[str, Any]]:
        """Load every top-level spec file into a list of dicts."""
        specs: list[dict[str, Any]] = []
        for path in self.all_spec_paths():
            data = yaml.safe_load(path.read_text()) or {}
            if isinstance(data, dict):
                data.setdefault("agent_type", path.stem)
                specs.append(data)
        return specs

    # ---- write --------------------------------------------------------

    def save(self, agent_type: str, spec: dict[str, Any], project: str | None = None) -> Path:
        """Write one spec to YAML (creating parent dirs) and return the path."""
        path = self.spec_path(agent_type, project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(spec, sort_keys=False))
        return path


# ---------------------------------------------------------------------------
# Validation — try PermissionSpecParser, fall back to structural check
# ---------------------------------------------------------------------------


def validate_spec(spec: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty = valid).

    Performs structural validation against the documented spec shape. When the
    parallel task lands a ``PermissionSpecParser`` in
    ``general_ludd.security.permissions``, this function prefers it and falls
    back to the structural check only if the parser is absent or raises.
    """
    parser_errors = _try_parser_validate(spec)
    if parser_errors is not None:
        return parser_errors
    return _structural_validate(spec)


def _try_parser_validate(spec: dict[str, Any]) -> list[str] | None:
    """Attempt validation via ``PermissionSpecParser`` if it exists; None on absence."""
    import importlib

    try:
        mod = importlib.import_module("general_ludd.security.permissions")
    except Exception:
        return None
    parser_cls = getattr(mod, "PermissionSpecParser", None)
    if parser_cls is None:
        return None
    try:
        result = parser_cls().validate(spec)
        if isinstance(result, list):
            return [str(e) for e in result]
    except Exception:
        return None
    return []


def _structural_validate(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    agent_type = spec.get("agent_type")
    if not agent_type or not isinstance(agent_type, str):
        errors.append("agent_type must be a non-empty string")
    caps = spec.get("capabilities")
    if caps is not None and not isinstance(caps, list):
        errors.append("capabilities must be a list")
    elif isinstance(caps, list):
        for i, cap in enumerate(caps):
            if not isinstance(cap, dict):
                errors.append(f"capabilities[{i}] must be a mapping")
                continue
            if not cap.get("resource"):
                errors.append(f"capabilities[{i}].resource is required")
            actions = cap.get("actions")
            if not isinstance(actions, list):
                errors.append(f"capabilities[{i}].actions must be a list")
    denied = spec.get("denied")
    if denied is not None and not isinstance(denied, list):
        errors.append("denied must be a list")
    ttl = spec.get("max_sts_ttl")
    if ttl is not None and (not isinstance(ttl, int) or ttl < 0):
        errors.append("max_sts_ttl must be a non-negative integer")
    return errors


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _resolve_config_dir(args: argparse.Namespace) -> Path:
    cd = getattr(args, "config_dir", None)
    if cd:
        return Path(cd).expanduser()
    return Path.home() / ".config" / "gludd"


def _emit(data: Any, args: argparse.Namespace) -> None:
    """Print ``data`` as JSON when --json, else rely on caller's human format."""
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))


def _actions_list(raw: str) -> list[str]:
    return [a.strip() for a in raw.split(",") if a.strip()]


def _parse_constraints(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"--constraints expects KEY=VAL, got: {item}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _http_with_psk(
    method: str,
    url: str,
    *,
    psk: str | None,
    json_body: Any = None,
    params: Any = None,
    timeout: float = 10.0,
    ok_codes: tuple[int, ...] = (200,),
) -> Any:
    headers: dict[str, str] = {}
    if psk:
        headers["Authorization"] = f"Bearer {psk}"
    try:
        m = method.upper()
        if m == "GET":
            resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        elif m == "POST":
            resp = httpx.post(url, json=json_body, params=params, headers=headers, timeout=timeout)
        elif m == "DELETE":
            resp = httpx.delete(url, params=params, headers=headers, timeout=timeout)
        elif m == "PUT":
            resp = httpx.put(url, json=json_body, params=params, headers=headers, timeout=timeout)
        elif m == "PATCH":
            resp = httpx.patch(url, json=json_body, params=params, headers=headers, timeout=timeout)
        else:
            resp = httpx.request(method, url, json=json_body, params=params, headers=headers, timeout=timeout)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code in ok_codes:
        try:
            return resp.json()
        except ValueError:
            return {"text": resp.text}
    print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand handlers — file-backed
# ---------------------------------------------------------------------------


def _cmd_perm_list(args: argparse.Namespace) -> None:
    store = SpecStore(_resolve_config_dir(args))
    specs = store.load_all()
    if getattr(args, "agent_type", None):
        specs = [s for s in specs if s.get("agent_type") == args.agent_type]
    if getattr(args, "json", False):
        _emit(specs, args)
        return
    if not specs:
        print("No permission specs found.")
        print(f"Config dir: {store.perms_dir}")
        return
    # Header: agent_type | capabilities | denied | max_sts_ttl
    print(f"{'agent_type':<20} {'capabilities':<40} {'denied':<20} {'max_sts_ttl'}")
    print("-" * 90)
    for s in specs:
        at = str(s.get("agent_type", "?"))[:19]
        caps = s.get("capabilities", [])
        cap_str = ", ".join(
            f"{c.get('resource', '?')}:{'/'.join(c.get('actions', []))}" for c in caps if isinstance(c, dict)
        )[:39]
        denied = s.get("denied", []) or []
        denied_str = str(len(denied)) if isinstance(denied, list) else "?"
        ttl = s.get("max_sts_ttl", "")
        print(f"{at:<20} {cap_str:<40} {denied_str:<20} {ttl}")


def _cmd_perm_show(args: argparse.Namespace) -> None:
    store = SpecStore(_resolve_config_dir(args))
    spec = store.load(args.agent_type)
    if spec is None:
        print(f"No spec for agent_type '{args.agent_type}'.", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "json", False):
        _emit(spec, args)
        return
    print(yaml.safe_dump(spec, sort_keys=False))


def _cmd_perm_grant(args: argparse.Namespace) -> None:
    store = SpecStore(_resolve_config_dir(args))
    spec = store.load(args.agent_type)
    if spec is None:
        spec = {"agent_type": args.agent_type, "capabilities": [], "denied": [], "max_sts_ttl": 3600}
    caps = spec.setdefault("capabilities", [])
    constraints = _parse_constraints(args.constraints)
    caps.append(
        {
            "resource": args.resource,
            "actions": _actions_list(args.actions),
            "constraints": constraints,
        }
    )
    errors = validate_spec(spec)
    if errors:
        print("Validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    store.save(args.agent_type, spec)
    print(f"Granted {args.actions} on {args.resource} to {args.agent_type}")


def _cmd_perm_deny(args: argparse.Namespace) -> None:
    store = SpecStore(_resolve_config_dir(args))
    spec = store.load(args.agent_type)
    if spec is None:
        spec = {"agent_type": args.agent_type, "capabilities": [], "denied": [], "max_sts_ttl": 3600}
    denied = spec.setdefault("denied", [])
    denied.append(
        {
            "resource": args.resource,
            "actions": _actions_list(args.actions),
        }
    )
    errors = validate_spec(spec)
    if errors:
        print("Validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    store.save(args.agent_type, spec)
    print(f"Denied {args.actions} on {args.resource} to {args.agent_type}")


def _cmd_perm_revoke(args: argparse.Namespace) -> None:
    store = SpecStore(_resolve_config_dir(args))
    spec = store.load(args.agent_type)
    if spec is None:
        print(f"No spec for agent_type '{args.agent_type}'.", file=sys.stderr)
        sys.exit(1)
    caps = spec.get("capabilities", []) or []
    new_caps = [c for c in caps if isinstance(c, dict) and c.get("resource") != args.resource]
    if len(new_caps) == len(caps):
        print(f"No capability on resource '{args.resource}' found.", file=sys.stderr)
        sys.exit(1)
    if not getattr(args, "yes", False):
        confirm = input(f"Revoke capabilities on '{args.resource}' from '{args.agent_type}'? [y/N] ")
        if confirm.strip().lower() not in {"y", "yes"}:
            print("Aborted.")
            return
    spec["capabilities"] = new_caps
    store.save(args.agent_type, spec)
    print(f"Revoked {args.resource} from {args.agent_type}")


def _cmd_perm_edit(args: argparse.Namespace) -> None:
    store = SpecStore(_resolve_config_dir(args))
    spec = store.load(args.agent_type)
    if spec is None:
        spec = {"agent_type": args.agent_type, "capabilities": [], "denied": [], "max_sts_ttl": 3600}
    path = store.spec_path(args.agent_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    editor = args.editor or os.environ.get("EDITOR", "vi")
    cmd = [editor, str(path)]
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"Editor exited with code {rc}; changes not validated.", file=sys.stderr)
        sys.exit(1)
    new_data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(new_data, dict):
        print("Invalid spec: not a mapping", file=sys.stderr)
        # restore original
        path.write_text(yaml.safe_dump(spec, sort_keys=False))
        sys.exit(1)
    # Validate the raw edited content BEFORE re-defaulting agent_type, so an
    # editor that deletes the agent_type key is correctly rejected.
    errors = validate_spec(new_data)
    if errors:
        print("Validation failed; original file restored:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        path.write_text(yaml.safe_dump(spec, sort_keys=False))
        sys.exit(1)
    new_data.setdefault("agent_type", args.agent_type)
    store.save(args.agent_type, new_data)
    print(f"Updated {path}")


def _cmd_perm_validate(args: argparse.Namespace) -> None:
    store = SpecStore(_resolve_config_dir(args))
    if args.agent_type:
        specs = [(args.agent_type, store.load(args.agent_type))]
        if specs[0][1] is None:
            print(f"No spec for agent_type '{args.agent_type}'.", file=sys.stderr)
            sys.exit(1)
    else:
        specs = [(s.get("agent_type", "?"), s) for s in store.load_all()]
    all_errors: dict[str, list[str]] = {}
    for at, spec in specs:
        if spec is None:
            continue
        errs = validate_spec(spec)
        if errs:
            all_errors[at or "?"] = errs
    if getattr(args, "json", False):
        print(json.dumps({"valid": not all_errors, "errors": all_errors}, indent=2))
        return
    if not all_errors:
        print(f"OK: {len(specs)} spec(s) valid")
        return
    for at, errs in all_errors.items():
        print(f"[{at}] INVALID:")
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
    sys.exit(1)


def _cmd_perm_diff(args: argparse.Namespace) -> None:
    store = SpecStore(_resolve_config_dir(args))
    a = store.load(args.agent_type_a)
    b = store.load(args.agent_type_b)
    if a is None:
        print(f"No spec: {args.agent_type_a}", file=sys.stderr)
        sys.exit(1)
    if b is None:
        print(f"No spec: {args.agent_type_b}", file=sys.stderr)
        sys.exit(1)
    a_caps: dict[str, dict[str, Any]] = {
        str(c.get("resource")): c for c in (a.get("capabilities") or []) if isinstance(c, dict) and c.get("resource")
    }
    b_caps: dict[str, dict[str, Any]] = {
        str(c.get("resource")): c for c in (b.get("capabilities") or []) if isinstance(c, dict) and c.get("resource")
    }
    only_a = {r: c for r, c in a_caps.items() if r not in b_caps}
    only_b = {r: c for r, c in b_caps.items() if r not in a_caps}
    common = {r: (a_caps[r], b_caps[r]) for r in a_caps if r in b_caps}
    diff_actions: dict[str, tuple[set[str], set[str]]] = {}
    for r, (ca, cb) in common.items():
        sa = set(ca.get("actions", []) or [])
        sb = set(cb.get("actions", []) or [])
        if sa != sb:
            diff_actions[r] = (sa, sb)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "only_in_a": {r: c for r, c in only_a.items()},
                    "only_in_b": {r: c for r, c in only_b.items()},
                    "action_diff": {r: {"a": sorted(a_), "b": sorted(b_)} for r, (a_, b_) in diff_actions.items()},
                },
                indent=2,
                default=str,
            )
        )
        return
    print(f"Diff: {args.agent_type_a}  vs  {args.agent_type_b}")
    print(f"\nOnly in {args.agent_type_a}:")
    for r, c in only_a.items():
        acts = "/".join(c.get("actions", []) or [])
        print(f"  + {r}  [{acts}]")
    print(f"\nOnly in {args.agent_type_b}:")
    for r, c in only_b.items():
        acts = "/".join(c.get("actions", []) or [])
        print(f"  + {r}  [{acts}]")
    if diff_actions:
        print("\nAction differences (shared resources):")
        for r, (sa, sb) in diff_actions.items():
            print(f"  {r}:")
            print(f"    {args.agent_type_a}: {sorted(sa)}")
            print(f"    {args.agent_type_b}: {sorted(sb)}")


def _cmd_perm_project(args: argparse.Namespace) -> None:
    store = SpecStore(_resolve_config_dir(args))
    if args.set_default_agent_type:
        at = args.set_default_agent_type
        base = store.load(at)
        if base is None:
            base = {"agent_type": at, "capabilities": [], "denied": [], "max_sts_ttl": 3600}
        path = store.save(at, base, project=args.project_name)
        print(f"Project override written: {path}")
        return
    # no flag: list project overrides
    proj_dir = store.perms_dir / "projects" / args.project_name
    if not proj_dir.is_dir():
        print(f"No project overrides for '{args.project_name}'.")
        return
    for p in sorted(proj_dir.glob("*.yml")):
        print(f"  {p.stem}")


# ---------------------------------------------------------------------------
# Subcommand handlers — HTTP-backed
# ---------------------------------------------------------------------------


def _resolve_psk(args: argparse.Namespace) -> str:
    psk = getattr(args, "psk", None) or os.environ.get("GLUDD_AUTH_PSK", "").strip()
    return psk or ""


def _cmd_perm_sts_list(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if getattr(args, "agent_id", None):
        params["agent_id"] = args.agent_id
    if getattr(args, "active_only", False):
        params["active_only"] = True
    data = _http_with_psk(
        "GET",
        f"{args.daemon_url}/admin/sts/active",
        psk=_resolve_psk(args),
        params=params,
    )
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return
    tokens = (data or {}).get("tokens", [])
    if not tokens:
        print("No STS tokens.")
        return
    print(f"{'token_id':<24} {'subject':<24} {'expires'}")
    print("-" * 70)
    for t in tokens:
        print(f"{str(t.get('token_id', '?'))[:23]:<24} {str(t.get('subject', '?'))[:23]:<24} {t.get('expires_at', '')}")


def _cmd_perm_sts_issue(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec_yaml).expanduser()
    if not spec_path.exists():
        print(f"Spec file not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = yaml.safe_load(spec_path.read_text()) or {}
    body: dict[str, Any] = {
        "subject_agent_id": args.subject_agent_id,
        "spec": spec,
    }
    if getattr(args, "ttl", None):
        body["ttl"] = args.ttl
    data = _http_with_psk(
        "POST",
        f"{args.daemon_url}/admin/sts/issue",
        psk=_resolve_psk(args),
        json_body=body,
    )
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return
    token = (data or {}).get("token", "")
    expires = (data or {}).get("expires_at", "")
    print(f"Token: {token}")
    print(f"Expires: {expires}")


def _cmd_perm_sts_inspect(args: argparse.Namespace) -> None:
    data = _http_with_psk(
        "GET",
        f"{args.daemon_url}/admin/sts/audit",
        psk=_resolve_psk(args),
        params={"token_id": args.token_id},
    )
    print(json.dumps(data, indent=2, default=str))


def _cmd_perm_sts_revoke(args: argparse.Namespace) -> None:
    data = _http_with_psk(
        "DELETE",
        f"{args.daemon_url}/admin/sts/{args.token_id}",
        psk=_resolve_psk(args),
    )
    print(json.dumps(data, indent=2, default=str))


def _cmd_perm_audit(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if getattr(args, "agent_id", None):
        params["agent_id"] = args.agent_id
    if getattr(args, "since", None):
        params["since"] = args.since
    if getattr(args, "capability", None):
        params["capability"] = args.capability
    data = _http_with_psk(
        "GET",
        f"{args.daemon_url}/admin/sts/audit",
        psk=_resolve_psk(args),
        params=params,
    )
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return
    events = (data or {}).get("events", [])
    if not events:
        print("No audit events.")
        return
    print(f"{'time':<24} {'issuer':<16} {'subject':<16} {'capability':<16} {'target':<16} {'event'}")
    print("-" * 100)
    for e in events:
        print(
            f"{str(e.get('time', '?'))[:23]:<24} "
            f"{str(e.get('issuer', '?'))[:15]:<16} "
            f"{str(e.get('subject', '?'))[:15]:<16} "
            f"{str(e.get('capability', '?'))[:15]:<16} "
            f"{str(e.get('target', '?'))[:15]:<16} "
            f"{e.get('event', '')}"
        )


# ---------------------------------------------------------------------------
# Subcommand handlers — escalations
# ---------------------------------------------------------------------------


def _cmd_perm_escalations_list(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if getattr(args, "status", None):
        params["status"] = args.status
    data = _http_with_psk(
        "GET",
        f"{args.daemon_url}/admin/perm/escalations",
        psk=_resolve_psk(args),
        params=params,
    )
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return
    items = (data or {}).get("items", [])
    if not items:
        print("No escalation requests.")
        return
    print(f"{'id':<6} {'agent_id':<24} {'status':<16} {'reason'}")
    print("-" * 80)
    for it in items:
        print(
            f"{it.get('id', '?')!s:<6} "
            f"{str(it.get('agent_id', '?'))[:23]:<24} "
            f"{str(it.get('status', '?'))[:15]:<16} "
            f"{str(it.get('reason', '?'))[:40]}"
        )


def _cmd_perm_escalations_approve(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {}
    if getattr(args, "reason", None):
        body["reason"] = args.reason
    data = _http_with_psk(
        "POST",
        f"{args.daemon_url}/admin/perm/escalations/{args.escalation_id}/approve",
        psk=_resolve_psk(args),
        json_body=body,
    )
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return
    print(f"Escalation {args.escalation_id}: {data.get('status', '?')}")
    if data.get("sts_token_id"):
        print(f"  STS token: {data['sts_token_id']}")


def _cmd_perm_escalations_deny(args: argparse.Namespace) -> None:
    data = _http_with_psk(
        "POST",
        f"{args.daemon_url}/admin/perm/escalations/{args.escalation_id}/deny",
        psk=_resolve_psk(args),
        json_body={"reason": args.reason},
    )
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return
    print(f"Escalation {args.escalation_id}: {data.get('status', '?')}")


def _cmd_perm_escalations_history(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if getattr(args, "agent_id", None):
        params["agent_id"] = args.agent_id
    data = _http_with_psk(
        "GET",
        f"{args.daemon_url}/admin/perm/escalations/history",
        psk=_resolve_psk(args),
        params=params,
    )
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return
    items = (data or {}).get("items", [])
    if not items:
        print("No escalation history.")
        return
    print(f"{'id':<6} {'agent_id':<24} {'status':<16} {'created_at'}")
    print("-" * 80)
    for it in items:
        print(
            f"{it.get('id', '?')!s:<6} "
            f"{str(it.get('agent_id', '?'))[:23]:<24} "
            f"{str(it.get('status', '?'))[:15]:<16} "
            f"{str(it.get('created_at', '?'))[:30]}"
        )


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    """Register the `perm` subcommand tree on the given subparsers."""
    perm_parser = subparsers.add_parser(
        "perm",
        help="Permission system: inspect and edit PermissionSpecs and STS tokens.",
    )
    perm_parser.set_defaults(func=None)
    perm_sub = perm_parser.add_subparsers(dest="perm_command")

    # list
    p = perm_sub.add_parser("list", help="List configured PermissionSpecs.")
    p.add_argument("--agent-type", default=None, help="Filter to one agent type.")
    _add_common(p)
    p.set_defaults(func=_cmd_perm_list)

    # show
    p = perm_sub.add_parser("show", help="Print the full YAML spec for one agent type.")
    p.add_argument("agent_type", help="Agent type to show.")
    _add_common(p)
    p.set_defaults(func=_cmd_perm_show)

    # grant
    p = perm_sub.add_parser("grant", help="Add a capability to an agent-type's spec.")
    p.add_argument("agent_type", help="Agent type to modify.")
    p.add_argument("resource", help="Resource identifier (e.g. file:repo).")
    p.add_argument("actions", help="Comma-separated actions (e.g. read,write).")
    p.add_argument("--constraints", nargs="*", default=None, help="KEY=VAL constraint pairs.")
    _add_common(p)
    p.set_defaults(func=_cmd_perm_grant)

    # deny
    p = perm_sub.add_parser("deny", help="Add to the denied list.")
    p.add_argument("agent_type", help="Agent type to modify.")
    p.add_argument("resource", help="Resource identifier.")
    p.add_argument("actions", help="Comma-separated actions.")
    _add_common(p)
    p.set_defaults(func=_cmd_perm_deny)

    # revoke
    p = perm_sub.add_parser("revoke", help="Remove a capability (by resource).")
    p.add_argument("agent_type", help="Agent type to modify.")
    p.add_argument("resource", help="Resource to revoke.")
    p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation.")
    _add_common(p)
    p.set_defaults(func=_cmd_perm_revoke)

    # edit
    p = perm_sub.add_parser("edit", help="Open the spec YAML in $EDITOR; validate on save.")
    p.add_argument("agent_type", help="Agent type to edit.")
    p.add_argument("--editor", default=None, help="Editor binary (default: $EDITOR or vi).")
    _add_common(p)
    p.set_defaults(func=_cmd_perm_edit)

    # validate
    p = perm_sub.add_parser("validate", help="Validate one or all specs; exit 0/1.")
    p.add_argument("agent_type", nargs="?", default=None, help="Validate just this type.")
    _add_common(p)
    p.set_defaults(func=_cmd_perm_validate)

    # diff
    p = perm_sub.add_parser("diff", help="Show capability diff between two specs.")
    p.add_argument("agent_type_a")
    p.add_argument("agent_type_b")
    _add_common(p)
    p.set_defaults(func=_cmd_perm_diff)

    # project
    p = perm_sub.add_parser("project", help="Per-project permission overrides.")
    p.add_argument("project_name", help="Project name.")
    p.add_argument("--set-default-agent-type", default=None, help="Seed a project override from a system default.")
    _add_common(p)
    p.set_defaults(func=_cmd_perm_project)

    # sts (group)
    sts_parser = perm_sub.add_parser("sts", help="STS token operations (daemon-backed).")
    sts_parser.set_defaults(func=None)
    sts_sub = sts_parser.add_subparsers(dest="perm_sts_command")

    sp = sts_sub.add_parser("list", help="List STS tokens.")
    sp.add_argument("--agent-id", default=None)
    sp.add_argument("--active-only", action="store_true")
    _add_http_common(sp)
    sp.set_defaults(func=_cmd_perm_sts_list)

    sp = sts_sub.add_parser("issue", help="Issue an STS token.")
    sp.add_argument("subject_agent_id", help="Subject agent ID.")
    sp.add_argument("--spec-yaml", required=True, help="Path to a PermissionSpec YAML.")
    sp.add_argument("--ttl", type=int, default=None, help="TTL in seconds.")
    _add_http_common(sp)
    sp.set_defaults(func=_cmd_perm_sts_issue)

    sp = sts_sub.add_parser("inspect", help="Inspect one token + its audit history.")
    sp.add_argument("token_id")
    _add_http_common(sp)
    sp.set_defaults(func=_cmd_perm_sts_inspect)

    sp = sts_sub.add_parser("revoke", help="Revoke an STS token immediately.")
    sp.add_argument("token_id")
    _add_http_common(sp)
    sp.set_defaults(func=_cmd_perm_sts_revoke)

    # audit
    p = perm_sub.add_parser("audit", help="Query the permission audit log.")
    p.add_argument("--agent-id", default=None)
    p.add_argument("--since", default=None, help="ISO timestamp lower bound.")
    p.add_argument("--capability", default=None)
    _add_http_common(p)
    p.set_defaults(func=_cmd_perm_audit)

    return perm_parser


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config-dir", default=None, help="Config directory (default: ~/.config/gludd).")
    p.add_argument("--json", dest="json", action="store_true", help="Emit machine-readable JSON.")
    p.add_argument("--quiet", action="store_true", help="Suppress non-essential output.")


def _add_http_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--daemon-url", default="http://localhost:8000", help="Daemon base URL.")
    p.add_argument("--psk", default=None, help="Pre-shared key (or set GLUDD_AUTH_PSK).")
    p.add_argument("--config-dir", default=None, help="Config directory (default: ~/.config/gludd).")
    p.add_argument("--json", dest="json", action="store_true", help="Emit machine-readable JSON.")
    p.add_argument("--quiet", action="store_true", help="Suppress non-essential output.")
