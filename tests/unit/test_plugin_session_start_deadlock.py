"""Verify no plugin deadlocks the session-start 6-read sequence.

The session-start protocol requires reading TASKS.md, BUGS.md, SESSION.md,
config/ratchet.yml + running git-status + git-log. If any tool.execute.before
plugin denies these reads, the agent cannot onboard and the session deadlocks.
"""
import json
import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_DIR = os.path.join(PROJECT_ROOT, ".opencode", "plugin")
LIB_DIR = os.path.join(PROJECT_ROOT, ".opencode", "lib")

SESSION_START_TOOL_COUNT = 6  # TASKS.md, BUGS.md, SESSION.md, ratchet.yml, git-status, git-log
HARD_ON_PLUGINS = frozenset({"enforce-no-suppressions.ts"})


def all_plugin_files():
    """Return all .ts plugin files."""
    assert os.path.isdir(PLUGIN_DIR), "Required .opencode/plugin directory is missing"
    return sorted(f for f in os.listdir(PLUGIN_DIR) if f.endswith(".ts"))


def read_plugin(fname):
    with open(os.path.join(PLUGIN_DIR, fname)) as f:
        return f.read()


def plugin_has_default_impl(content):
    """Check if the plugin has a defaultImpl with a tool.execute.before hook."""
    if '"tool.execute.before"' not in content:
        return False
    m = re.search(r'defaultImpl[^=]*=\s*\{[^}]*"tool\.execute\.before"', content, re.DOTALL)
    return bool(m)


def has_tool_type_filter(content):
    """Check if the plugin filters by tool type before denying.
    Returns set of tool types it gates."""
    filters = set()
    if re.search(r'tool\s*(===|!==)\s*"bash"', content):
        filters.add("bash")
    if re.search(r'tool\s*(===|!==)\s*"edit"', content):
        filters.add("edit")
    if re.search(r'tool\s*(===|!==)\s*"write"', content):
        filters.add("write")
    if re.search(r'isDispatchTool|getDispatchTools\(\)', content):
        filters.add("dispatch")
    if re.search(r'isReadTool\(', content):
        filters.add("read-excluded")
    return filters


class TestSessionStartNoDeadlock:
    """Session-start requires 6 reads. No plugin may deny them."""

    def test_all_tool_execute_before_plugins_have_disable_env(self):
        """Every tool.execute.before plugin must have a disable env var."""
        missing = []
        for fname in all_plugin_files():
            content = read_plugin(fname)
            if not plugin_has_default_impl(content):
                continue
            if fname in HARD_ON_PLUGINS:
                continue
            has_disable = bool(re.search(
                r'process\.env\.GLUDD_\w+_ENFORCE', content
            ))
            if not has_disable:
                missing.append(f"{fname} (no GLUDD_*_ENFORCE disable)")
        assert not missing, (
            f"Plugins with tool.execute.before but no disable env: {missing}"
        )

    def test_no_plugin_denies_reads_at_session_start(self):
        """Every tool.execute.before plugin must either:
        (a) only gate specific tool types (bash, edit, write, dispatch), OR
        (b) explicitly exclude reads (isReadTool), OR
        (c) have a session-start grace period.

        A plugin that returns deny for ALL tool types with no read exclusion
        will deadlock the session-start 6-read sequence.
        """
        violations = []
        for fname in all_plugin_files():
            content = read_plugin(fname)
            if not plugin_has_default_impl(content):
                continue
            filters = has_tool_type_filter(content)
            if filters and "read-excluded" not in filters:
                deny_after = content.split('"tool.execute.before"')[-1]
                if 'permissionDecision' not in deny_after:
                    continue
                if all(t in filters for t in ["bash", "edit", "write", "dispatch"]):
                    continue
            if "read-excluded" in filters:
                continue
            if re.search(
                r'sessionPrimed|SESSION_START.*GRACE|FRESH_SECS|DISPATCH_NOW_SECS',
                content
            ):
                continue
            violations.append(
                f"  {fname}: tool.execute.before without read exclusion or session grace "
                f"(filters: {sorted(filters)})"
            )
        assert not violations, (
            "Plugins that may deny reads at session start:\n" + "\n".join(violations)
        )

    def test_enforce_context_has_read_exclusion(self):
        """enforce-context.ts must not deny read tools. This was the confirmed
        deadlock vector: when SESSION.md was >24h stale, all tools were denied."""
        fpath = os.path.join(PLUGIN_DIR, "enforce-context.ts")
        assert os.path.isfile(fpath), "Required enforce-context.ts plugin is missing"
        with open(fpath) as f:
            content = f.read()
        assert "isReadTool" in content, (
            "enforce-context.ts missing isReadTool guard"
        )
        assert 'GLUDD_CONTEXT_ENFORCE' in content, (
            "enforce-context.ts missing disable env var"
        )

    def test_read_block_thresholds_do_not_trigger_in_6_reads(self):
        """Any counter-based thresholds must not trigger within 6 reads."""
        violations = []
        for fname in all_plugin_files():
            content = read_plugin(fname)
            if not plugin_has_default_impl(content):
                continue
            m = re.search(r'MAINTHREAD_THRESHOLD\s*=.*?["\'](\d+)["\']', content)
            if m and int(m.group(1)) <= SESSION_START_TOOL_COUNT and not re.search(
                r'isReadTool|isMainthreadTool.*tool', content
            ):
                violations.append(
                    f"  {fname}: MAINTHREAD_THRESHOLD={m.group(1)} ≤ {SESSION_START_TOOL_COUNT} (reads count)"
                )
            m = re.search(
                r'CONSECUTIVE_NON_DISPATCH_THRESHOLD\s*=.*?["\'](\d+)["\']', content
            )
            if m and int(m.group(1)) <= SESSION_START_TOOL_COUNT and not re.search(
                r'isReadTool\(tool\)', content
            ):
                violations.append(
                    f"  {fname}: CONSECUTIVE_NON_DISPATCH_THRESHOLD="
                    f"{m.group(1)} ≤ {SESSION_START_TOOL_COUNT} (reads count)"
                )
        assert not violations, (
            "Plugins with counters ≤ session-start read count:\n"
            + "\n".join(violations)
        )


class TestPluginToolAwareness:
    """The agent must know which tools are available and what plugins enforce."""

    def test_plugin_count_matches_opencode_json(self):
        """opencode.json must reference all .ts files in plugin/ dir."""
        config_path = os.path.join(PROJECT_ROOT, "opencode.json")
        assert os.path.isfile(config_path), "Required opencode.json is missing"
        with open(config_path) as f:
            config = json.load(f)
        registered = set()
        for p in config.get("plugin", []):
            registered.add(os.path.basename(
                p.replace("./.opencode/plugin/", "").replace("./.opencode/plugins/", "")
            ))
        on_disk = set(f for f in os.listdir(PLUGIN_DIR) if f.endswith(".ts"))
        plugins_dir = os.path.join(PROJECT_ROOT, ".opencode", "plugins")
        on_disk_plugins = set(
            f for f in os.listdir(plugins_dir) if f.endswith(".ts")
        ) if os.path.isdir(plugins_dir) else set()
        all_disk = on_disk | on_disk_plugins
        unregistered = on_disk - registered
        assert not unregistered, (
            f"Plugin files NOT in opencode.json: {unregistered}"
        )
        missing = registered - all_disk
        assert not missing, (
            f"opencode.json references non-existent plugins: {missing}"
        )

    def test_list_plugins_target_exists(self):
        """The make list-plugins target must exist and produce output."""
        makefile = os.path.join(PROJECT_ROOT, "Makefile")
        assert os.path.exists(makefile)
        with open(makefile) as f:
            content = f.read()
        assert "list-plugins:" in content, "list-plugins target not in Makefile"

    def test_list_plugins_script_exists(self):
        """The scripts/list_plugins.py script must exist."""
        script = os.path.join(PROJECT_ROOT, "scripts", "list_plugins.py")
        assert os.path.exists(script), "scripts/list_plugins.py not found"

    def test_enforce_context_read_exclusion_is_in_default_impl(self):
        """The isReadTool guard must be inside the defaultImpl block, not just
        the proxy wrapper."""
        fpath = os.path.join(PLUGIN_DIR, "enforce-context.ts")
        assert os.path.isfile(fpath), "Required enforce-context.ts plugin is missing"
        with open(fpath) as f:
            content = f.read()
        m = re.search(
            r'const defaultImpl[^=]*=\s*(\{[^}]*(?:\{[^}]*\}[^}]*)*\})',
            content, re.DOTALL
        )
        assert m, "defaultImpl not found in enforce-context.ts"
        impl_body = m.group(1)
        assert "isReadTool" in impl_body, (
            "isReadTool guard NOT in enforce-context.ts defaultImpl"
        )
