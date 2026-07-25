"""BP.12: Enforcement plugin heartbeat infrastructure — structural test.

Verifies the three items from BP.12:
1. Every enforcement plugin calls reportAlive() (checking impl files for
   thin-wrapper plugins that delegate to impl).
2. The heartbeat file path pattern is /tmp/gludd-plugin-heartbeat-<plugin>.json
   as defined by writeHeartbeat() in shared.ts.
3. The verify-enforcement pipeline (gate phase + check-plugin-heartbeats) is
   wired into the gate and reads heartbeat files at the correct path pattern.

This test is structural (reads source files, no runtime invocation).
Runtime liveness verification is handled by verify_plugin_liveness.py.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
OPENCODE_JSON = REPO_ROOT / "opencode.json"
SHARED_TS = REPO_ROOT / ".opencode" / "lib" / "shared.ts"
LIVENESS_SCRIPT = REPO_ROOT / "scripts" / "verify_plugin_liveness.py"
MAKEFILE = REPO_ROOT / "Makefile"
PLUGIN_DIR = REPO_ROOT / ".opencode" / "plugin"

_HEARTBEAT_PATH_RE = re.compile(
    r"gludd-plugin-heartbeat-\$\{pluginName\}|"
    r"gludd-plugin-heartbeat-"
)


# ── helpers ──────────────────────────────────────────────────────────────

def _read_plugins_from_opencode_json() -> list[str]:
    """Return plugin stem names (e.g. \"enforce-floor\") from opencode.json."""
    cfg = json.loads(OPENCODE_JSON.read_text())
    names: list[str] = []
    for rel in cfg.get("plugin", []):
        rel_path = rel[2:] if rel.startswith("./") else rel
        names.append(Path(rel_path).stem)
    return names


def _enforcement_plugins() -> list[str]:
    """Plugin stems that start with 'enforce-' (excludes watchdog etc.)."""
    return [p for p in _read_plugins_from_opencode_json() if p.startswith("enforce-")]


def _read_make_target(target_name: str) -> str:
    """Extract a Makefile target's full text from the target line through recipe."""
    makefile = MAKEFILE.read_text()
    lines = makefile.splitlines()
    in_target = False
    recipe: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{target_name}:") and not in_target:
            in_target = True
            recipe.append(line)
            continue
        if in_target:
            is_body = line and not line.startswith("\t") and not line.startswith("@")
            has_colon = ":" in line and not line.startswith(" ") and not line.startswith("\t")
            _cmds = ("@", "$(", "$(MAKE)", "$(UV)", "rm", "mkdir", "echo",
                     "printf", "touch", "exit", "true", "false")
            if is_body and has_colon and not line.strip().startswith(_cmds):
                break
            recipe.append(line)
    return "\n".join(recipe)


_REPORT_ALIVE_IMPORT = re.compile(
    r'import\s+\{[^}]*\breportAlive\b[^}]*\}\s+from\s+"[^"]*shared\.ts"'
)
_REPORT_ALIVE_DEF = re.compile(r"function\s+_?reportAlive\s*\(")
_REPORT_ALIVE_CALL = re.compile(r"(?<!function\s)_?reportAlive\s*\(")

_IMPL_IMPORT_RE = re.compile(r'import\s+\w+\s+from\s+"(\.\/(?:impl\/)?([^"]+))"')


def _plugin_has_report_alive(plugin_name: str) -> bool:
    """Check whether a plugin (or its impl delegate) calls reportAlive()."""
    ts_file = PLUGIN_DIR / f"{plugin_name}.ts"
    if not ts_file.exists():
        return False
    src = ts_file.read_text()

    # Direct: defined or imported and called in the main plugin file
    has_def = bool(_REPORT_ALIVE_DEF.search(src))
    has_import = bool(_REPORT_ALIVE_IMPORT.search(src))
    src_no_def = _REPORT_ALIVE_DEF.sub("", src)
    has_call = bool(_REPORT_ALIVE_CALL.search(src_no_def))

    if (has_def or has_import) and has_call:
        return True

    # Thin wrapper: follow impl import and check the impl file
    impl_match = _IMPL_IMPORT_RE.search(src)
    if impl_match:
        impl_rel = impl_match.group(1)
        impl_path = (ts_file.parent / impl_rel).resolve()
        if impl_path.exists():
            impl_src = impl_path.read_text()
            impl_has_def = bool(_REPORT_ALIVE_DEF.search(impl_src))
            impl_has_import = bool(_REPORT_ALIVE_IMPORT.search(impl_src))
            impl_src_no_def = _REPORT_ALIVE_DEF.sub("", impl_src)
            impl_has_call = bool(_REPORT_ALIVE_CALL.search(impl_src_no_def))
            if (impl_has_def or impl_has_import) and impl_has_call:
                return True

    return False


# ── Item 1: Every enforcement plugin calls reportAlive() ─────────────────

def test_all_enforcement_plugins_call_report_alive() -> None:
    """Every enforcement plugin must call reportAlive() from at least one hook.

    For thin-wrapper plugins (enforce-make, enforce-stop) that delegate to
    impl files, this follows the indirection and checks the impl file.
    """
    missing: list[str] = []
    for name in _enforcement_plugins():
        if not _plugin_has_report_alive(name):
            missing.append(name)
    assert not missing, (
        f"{len(missing)}/{len(_enforcement_plugins())} enforcement plugins "
        f"don't call reportAlive(): {missing}"
    )


# ── Item 2: Heartbeat file path pattern ──────────────────────────────────

def test_write_heartbeat_path_pattern_in_shared_ts() -> None:
    """shared.ts writeHeartbeat() must write to the canonical path pattern."""
    src = SHARED_TS.read_text()
    assert "writeHeartbeat" in src, (
        "shared.ts must define writeHeartbeat() for per-plugin heartbeat files"
    )
    assert "gludd-plugin-heartbeat-" in src, (
        "shared.ts writeHeartbeat() must use path pattern "
        "/tmp/gludd-plugin-heartbeat-<pluginName>.json"
    )
    assert "${pluginName}" in src, (
        "writeHeartbeat() must include pluginName in the heartbeat file path"
    )


def test_heartbeat_path_matches_liveness_script() -> None:
    """verify_plugin_liveness.py must read heartbeat files at the same pattern."""
    script = LIVENESS_SCRIPT.read_text()
    assert "gludd-plugin-heartbeat-" in script, (
        "verify_plugin_liveness.py must read from the canonical heartbeat path "
        "/tmp/gludd-plugin-heartbeat-<name>.json"
    )
    assert "HEARTBEAT_DIR" in script, (
        "verify_plugin_liveness.py must use a configurable HEARTBEAT_DIR "
        "(default /tmp) for heartbeat file reads"
    )


def test_heartbeat_file_naming_is_consistent() -> None:
    """The path pattern is consistent across shared.ts, liveness script, and Makefile."""
    shared_src = SHARED_TS.read_text()
    script = LIVENESS_SCRIPT.read_text()
    makefile = MAKEFILE.read_text()

    for label, content in [
        ("shared.ts", shared_src),
        ("verify_plugin_liveness.py", script),
        ("Makefile", makefile),
    ]:
        assert "gludd-plugin-heartbeat-" in content, (
            f"Canonical heartbeat path pattern missing from {label}"
        )


# ── Item 3: verify-enforcement / check-plugin-heartbeats wiring ──────────

def test_check_plugin_heartbeats_target_exists() -> None:
    """The check-plugin-heartbeats make target must be defined."""
    makefile = MAKEFILE.read_text()
    assert "\ncheck-plugin-heartbeats:" in makefile, (
        "Makefile must define check-plugin-heartbeats target"
    )


def test_check_plugin_heartbeats_wired_into_check_all_guardrails() -> None:
    """check-all-guardrails must include check-plugin-heartbeats."""
    makefile = MAKEFILE.read_text()
    guardrails_line = None
    for line in makefile.splitlines():
        if line.strip().startswith("check-all-guardrails:"):
            guardrails_line = line
            break
    assert guardrails_line is not None, "check-all-guardrails target missing from Makefile"
    assert "check-plugin-heartbeats" in guardrails_line, (
        "check-all-guardrails must include check-plugin-heartbeats as a prerequisite"
    )


def test_verify_enforcement_gate_phase_exists() -> None:
    """The gate recipe must include a verify-enforcement phase."""
    recipe = _read_make_target("gate")
    assert "verify-enforcement" in recipe, (
        "gate recipe must include verify-enforcement phase: "
        "make verify-enforcement"
    )


def test_verify_enforcement_script_exists() -> None:
    """verify_enforcement.py must exist and be callable."""
    enforcement_script = REPO_ROOT / "scripts" / "verify_enforcement.py"
    assert enforcement_script.exists(), "scripts/verify_enforcement.py missing"
    script = enforcement_script.read_text()
    assert "ENFORCEMENT_PLUGINS" in script, (
        "verify_enforcement.py must include ENFORCEMENT_PLUGINS mapping"
    )
    recipe = _read_make_target("verify-enforcement")
    assert "verify_enforcement.py" in recipe, (
        "make verify-enforcement must call scripts/verify_enforcement.py"
    )


def test_check_plugin_heartbeats_calls_verify_plugin_liveness() -> None:
    """check-plugin-heartbeats must delegate to verify_plugin_liveness.py."""
    recipe = _read_make_target("check-plugin-heartbeats")
    assert "verify_plugin_liveness.py" in recipe, (
        "check-plugin-heartbeats must call verify_plugin_liveness.py"
    )


# ── Belt-and-braces: shared.ts must define writeHeartbeat, reportAlive ───

def test_shared_ts_defines_report_alive_and_write_heartbeat() -> None:
    """shared.ts is the single source of truth for both liveness helpers."""
    src = SHARED_TS.read_text()

    assert "export function reportAlive" in src, (
        "shared.ts must export function reportAlive()"
    )
    assert "export function writeHeartbeat" in src, (
        "shared.ts must export function writeHeartbeat()"
    )
    assert "ALIVE_PATH" in src and "/tmp/gludd-plugin-alive.json" in src, (
        "shared.ts ALIVE_PATH must default to /tmp/gludd-plugin-alive.json"
    )
    # writeHeartbeat uses a template literal with backticks: `/tmp/gludd-plugin-heartbeat-${pluginName}.json`
    assert "gludd-plugin-heartbeat-" in src and "${pluginName}" in src, (
        "shared.ts writeHeartbeat() must write to "
        "/tmp/gludd-plugin-heartbeat-<pluginName>.json"
    )


# ── Item 3b: Heartbeat paths scoped to /tmp/gludd-* (not arbitrary /tmp) ──

def test_alive_path_scoped_to_gludd_namespace() -> None:
    """ALIVE_PATH must live under /tmp/gludd-* — not an arbitrary /tmp/ path.

    A heartbeat file at /tmp/foo.json would escape the session-state cleanup
    targets (make clean-tmp, /tmp/gludd-* disk guards) and leak across
    sessions. The path must start with the canonical /tmp/gludd- prefix.
    """
    src = SHARED_TS.read_text()
    # Extract the default string literal for ALIVE_PATH
    m = re.search(r'ALIVE_PATH\s*=\s*process\.env\.\w+\s*\|\|\s*"([^"]+)"', src)
    assert m, "ALIVE_PATH default string literal not found in shared.ts"
    default_path = m.group(1)
    assert default_path.startswith("/tmp/gludd-"), (
        f"ALIVE_PATH default '{default_path}' must start with /tmp/gludd- "
        f"(not arbitrary /tmp/) so session cleanup and disk guards catch it"
    )


def test_write_heartbeat_path_scoped_to_gludd_namespace() -> None:
    """writeHeartbeat() path template must also be under /tmp/gludd-*."""
    src = SHARED_TS.read_text()
    # Find the template literal inside writeHeartbeat
    hb_match = re.search(
        r'writeHeartbeat[^`]*`([^`]*gludd-plugin-heartbeat[^`]*)`',
        src,
        re.DOTALL,
    )
    assert hb_match, "writeHeartbeat template literal not found in shared.ts"
    template_path = hb_match.group(1)
    assert template_path.startswith("/tmp/gludd-"), (
        f"writeHeartbeat path '{template_path}' must start with /tmp/gludd- "
        f"(not arbitrary /tmp/)"
    )


# ── Item 4: Heartbeat payload includes a timestamp (not just a flag) ──────

def _function_body(src: str, fn_name: str) -> str:
    """Extract a function body by line range (robust to nested braces).

    Finds `export function <fn_name>` and returns all lines from the signature
    line through the closing brace at column 0 of the following function
    (or end of file). Cheaper and more reliable than a brace-counting regex.
    """
    lines = src.splitlines()
    start = None
    for i, line in enumerate(lines):
        if f"export function {fn_name}" in line:
            start = i
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start + 1:]:
        # A closing brace at column 0 marks the end of the function
        if line.startswith("}"):
            break
        body.append(line)
    return "\n".join(body)


def test_report_alive_writes_timestamp_fields() -> None:
    """reportAlive() must write timestamp fields (last_seen / ts / loaded),
    not just a boolean flag.

    A bare {"alive": true} cannot age — the 60s stale threshold is computed
    from the timestamp delta. Without a numeric timestamp, no liveness check
    can distinguish a live plugin from a dead one."""
    src = SHARED_TS.read_text()
    body = _function_body(src, "reportAlive")
    assert body, "reportAlive function body not found in shared.ts"
    # Must assign at least one timestamp-bearing field
    ts_fields = [f for f in ("last_seen", "ts", "loaded") if f in body]
    assert ts_fields, (
        "reportAlive body must assign at least one timestamp field "
        f"(last_seen / ts / loaded). Found none. Body:\n{body}"
    )
    # Must call Date.now() to produce a numeric timestamp
    assert "Date.now()" in body, (
        "reportAlive must capture a numeric timestamp via Date.now() — "
        "a boolean flag cannot age"
    )


def test_write_heartbeat_writes_timestamp_field() -> None:
    """writeHeartbeat() payload must include a `ts` (or equivalent) timestamp
    field, not just {plugin, pid}."""
    src = SHARED_TS.read_text()
    body = _function_body(src, "writeHeartbeat")
    assert body, "writeHeartbeat function body not found in shared.ts"
    assert "Date.now()" in body, (
        "writeHeartbeat must call Date.now() to stamp the heartbeat with a "
        "numeric timestamp — a bare flag cannot age"
    )
    assert "ts:" in body, (
        "writeHeartbeat payload must include a `ts` field — the liveness "
        "checker reads hb['ts'] to compute age"
    )


# ── Item 5: Enumerate plugins with vs without heartbeat calls ─────────────

def test_heartbeat_adoption_report() -> None:
    """Enumerate every enforcement plugin and classify it as heartbeat-wired
    or heartbeat-missing. Prints the classification for diagnostics.

    This test PASSES regardless of the split — it is a reporting test that
    surfaces which plugins have adopted reportAlive() and which have not.
    The hard gate is test_all_enforcement_plugins_call_report_alive() above;
    this test makes the adoption visible without failing the gate.
    """
    plugins = _enforcement_plugins()
    with_hb = sorted(p for p in plugins if _plugin_has_report_alive(p))
    without_hb = sorted(p for p in plugins if not _plugin_has_report_alive(p))

    # Print for visibility (pytest -s to see it)
    print(f"\n heartbeat adoption: {len(with_hb)}/{len(plugins)} wired")
    print(f"  WITH heartbeat   : {with_hb}")
    if without_hb:
        print(f"  WITHOUT heartbeat: {without_hb}")
    else:
        print("  WITHOUT heartbeat: [] (full adoption)")

    # Sanity: the union must equal the full plugin list
    assert sorted(with_hb + without_hb) == sorted(plugins), (
        "plugin classification is inconsistent — union != full list"
    )
    # Sanity: a plugin cannot be in both lists
    assert not (set(with_hb) & set(without_hb)), (
        "a plugin appears in both WITH and WITHOUT lists — classification bug"
    )
