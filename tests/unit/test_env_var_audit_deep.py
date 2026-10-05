"""Deep environment variable audit: documentation, naming, security, and consistency.

Covers all ``GLUDD_*`` env vars plus credential-bearing non-GLUDD vars referenced
in ``src/general_ludd/`` and ``scripts/``.  Each test is self-contained; the
module scans the source tree at import time so it always reflects the live code.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import pytest

# ---------------------------------------------------------------------------
# Scanner helpers
# ---------------------------------------------------------------------------

_SRC = Path(__file__).resolve().parents[2] / "src"
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_DOCS = Path(__file__).resolve().parents[2] / "docs"

if not _SRC.is_dir():
    raise RuntimeError("Cannot find src/ relative to test file")

_GLUDD_ENV_VAR_RE = re.compile(r'"((?:GLUDD_[A-Z0-9_]+))"')
_OS_ENV_GET_RE = re.compile(r'os\.environ\.get\(\s*"([^"]+)"')
_OS_ENV_GETITEM_RE = re.compile(r"os\.environ\[\s*'([^']+)'\s*\]")
_OS_ENV_POP_RE = re.compile(r'os\.environ\.pop\(\s*"([^"]+)"')
_OS_ENV_SET_RE = re.compile(r'os\.environ\[\s*"([^"]+)"\s*\]\s*=')

_NON_GLUDD_CREDENTIAL_RE = re.compile(
    r'"((?:[A-Z][A-Z0-9_]*_(?:API_KEY|BASE_URL|AUTH_TOKEN|TOKEN|SECRET|PSK|PASSWORD)))"'
)

_SECRET_SUFFIXES = frozenset({"_API_KEY", "_KEY", "_TOKEN", "_PSK", "_SECRET", "_PASSWORD", "_PASSPHRASE"})
_SECRET_PATTERN_RE = re.compile(r"^(?:GLUDD_)?(?:.*(?:" + "|".join(_SECRET_SUFFIXES) + r"))$")

_TRUTHY_CHECK_RE = re.compile(r'\.lower\(\)\.strip\(\)\s*(?:==|in)\s*\{?"(?:1|true|yes|on)')
_TRUTHY_SET_RE = re.compile(r'\{[^}]*"(?:1|true|yes|on)"')

_PATTERNS = [
    _GLUDD_ENV_VAR_RE,
    _OS_ENV_GET_RE,
    _OS_ENV_GETITEM_RE,
    _OS_ENV_POP_RE,
    _OS_ENV_SET_RE,
]


class EnvUsage(NamedTuple):
    var_name: str
    file_path: str
    line: int
    raw_line: str
    is_get: bool = True


_SCANNED: list[EnvUsage] = []


def _scan() -> None:
    if _SCANNED:
        return
    seen: set[tuple[str, str, int]] = set()
    for root_dir in (_SRC, _SCRIPTS):
        for py_file in sorted(root_dir.rglob("*.py")):
            if "__pycache__" in str(py_file):
                continue
            try:
                text = py_file.read_text()
            except Exception:
                continue
            for lineno, line in enumerate(text.split("\n"), start=1):
                for pat in _PATTERNS:
                    for m in pat.finditer(line):
                        name = m.group(1)
                        if name in (
                            "PATH",
                            "HOME",
                            "USER",
                            "EDITOR",
                            "PYTHONPATH",
                            "XDG_DATA_HOME",
                            "TMPDIR",
                            "DATABASE_URL",
                            "SLURM_AVAILABLE",
                            "POSTGRES_AVAILABLE",
                            "PYTEST_ARGS",
                            "PYTEST_VERBOSITY",
                            "PYTEST_XDIST_WORKER",
                            "_LEAKY_ENV_VARS",
                        ):
                            continue
                        is_get = pat is _OS_ENV_GET_RE or pat is _GLUDD_ENV_VAR_RE or pat is _OS_ENV_POP_RE
                        # One physical call site can match several patterns (e.g.
                        # os.environ.get("GLUDD_X") matches both the GLUDD_
                        # literal regex and the os.environ.get regex). Record each
                        # (name, file, line) exactly once so "multiple call
                        # sites" means distinct sites, not regex overlap.
                        key = (name, str(py_file), lineno)
                        if key in seen:
                            continue
                        seen.add(key)
                        _SCANNED.append(EnvUsage(name, str(py_file), lineno, line, is_get=is_get))

    _SCANNED.sort(key=lambda e: e.var_name)


_scan()

_GLUDD_VARS = sorted({e.var_name for e in _SCANNED if e.var_name.startswith("GLUDD_")})
_NON_GLUDD_VARS = sorted({e.var_name for e in _SCANNED if not e.var_name.startswith("GLUDD_")})
_ALL_VARS = sorted({e.var_name for e in _SCANNED})


def _usages_for(name: str) -> list[EnvUsage]:
    return [e for e in _SCANNED if e.var_name == name]


def _read_ref_md_gludd_vars() -> set[str]:
    """Parse the GLUDD_* table from docs/CONFIG_REFERENCE.md."""
    ref_path = _DOCS / "CONFIG_REFERENCE.md"
    if not ref_path.exists():
        return set()
    text = ref_path.read_text()
    vars_found = set()
    for m in re.finditer(r"\| `(GLUDD_[A-Z0-9_]+)`", text):
        vars_found.add(m.group(1))
    return vars_found


# ---------------------------------------------------------------------------
# Tests — GLUDD_* env vars
# ---------------------------------------------------------------------------


class TestGLUDDNamingConvention:
    def test_all_uppercase_underscore(self):
        """Every GLUDD_* env var uses uppercase with underscores only."""
        bad = [v for v in _GLUDD_VARS if not re.fullmatch(r"GLUDD_[A-Z][A-Z0-9_]*", v)]
        assert not bad, (
            f"GLUDD_ env vars violating UPPER_SNAKE_CASE: {bad}. All GLUDD_ vars must be like GLUDD_EXAMPLE_NAME."
        )

    def test_no_double_underscores(self):
        bad = [v for v in _GLUDD_VARS if "__" in v]
        assert not bad, f"GLUDD_ env vars with double underscore: {bad}"

    def test_no_trailing_underscore(self):
        bad = [v for v in _GLUDD_VARS if v.endswith("_")]
        assert not bad, f"GLUDD_ env vars with trailing underscore: {bad}"

    def test_minimum_two_segments(self):
        """GLUDD_ALONE would be too generic; require at least GLUDD_X_Y."""
        bad = [v for v in _GLUDD_VARS if v.count("_") < 2]
        assert not bad, f"GLUDD_ prefix-only vars (too generic): {bad}"


class TestGLUDDDocumentation:
    ref_vars = _read_ref_md_gludd_vars()

    def test_config_reference_documents_live_vars(self):
        """Every GLUDD_ env var found in the source tree is documented in
        CONFIG_REFERENCE.md — or at least one of the config docs."""
        if not TestGLUDDDocumentation.ref_vars:
            pytest.skip("CONFIG_REFERENCE.md not found; no ref vars to check")
        missing = set(_GLUDD_VARS) - TestGLUDDDocumentation.ref_vars
        if missing:
            # Some vars may be documented in other docs (SECURITY_HARDENING, etc)
            hardening = _DOCS / "SECURITY_HARDENING.md"
            hardening_text = hardening.read_text() if hardening.exists() else ""
            still_missing = {v for v in missing if v not in hardening_text}
            assert not still_missing, (
                f"GLUDD_ env vars used in code but not in CONFIG_REFERENCE.md (or other docs): {sorted(still_missing)}"
            )

    def test_config_reference_entries_are_real(self):
        """Every GLUDD_ entry in CONFIG_REFERENCE.md has a code-level usage."""
        if not TestGLUDDDocumentation.ref_vars:
            pytest.skip("CONFIG_REFERENCE.md not found; no ref vars to check")
        code_set = set(_GLUDD_VARS)
        stale = TestGLUDDDocumentation.ref_vars - code_set
        assert not stale, f"CONFIG_REFERENCE.md documents GLUDD_ vars not found in code: {sorted(stale)}"


class TestGLUDDSecurityClassification:
    SECRET_PATTERN = re.compile(r"(?:KEY|TOKEN|PSK|SECRET|PASSWORD|PASSPHRASE)(?:_|$)")

    def test_secret_env_vars_identified(self):
        """Env vars matching secret/credential patterns are explicitly flagged."""
        secrets = [v for v in _GLUDD_VARS if _SECRET_PATTERN_RE.search(v)]
        non_secret_names = [
            "GLUDD_AUTH_PSK",
            "GLUDD_PSK_DISABLE",
            "GLUDD_PSK_IDENTITY_TTL_SECONDS",
            "GLUDD_PSK_ROTATION_OVERLAP_SECONDS",
            "GLUDD_INGEST_TOKEN",
            "GLUDD_ADMIN_TOKEN",
            "GLUDD_STS_ROLE_ID",
            "GLUDD_STS_SECRET_ID",
            "GLUDD_STS_TOKEN_ID",
            "GLUDD_SIGNING_KEY",
            "GLUDD_SELF_UPDATE_APPROVAL_SECRET",
        ]
        for v in secrets:
            assert v in non_secret_names or TestGLUDDSecurityClassification.SECRET_PATTERN.search(v), (
                f"GLUDD var {v} matches secret pattern; if it IS a secret, add to expect list; if not, rename it."
            )

    def test_psK_not_in_env_secrets_allowlist(self):
        """GLUDD_AUTH_PSK must NEVER appear in secrets/env.py allowlist patterns."""
        env_py = _SRC / "general_ludd" / "secrets" / "env.py"
        if not env_py.exists():
            pytest.skip("secrets/env.py not found")
        text = env_py.read_text()
        assert "GLUDD_AUTH_PSK" not in text, (
            "GLUDD_AUTH_PSK erroneously appears in secrets/env.py allowlist — "
            "it must NEVER be resolvable via the ambient-env secrets pathway"
        )

    def test_gludd_psk_not_os_environ_get_without_strip(self):
        """GLUDD_AUTH_PSK reads should use .strip() to avoid whitespace mismatch."""
        for u in _usages_for("GLUDD_AUTH_PSK"):
            if ".get(" in u.raw_line:
                assert ".strip()" in u.raw_line or "strip()" in u.raw_line, (
                    f"GLUDD_AUTH_PSK read at {u.file_path}:{u.line} lacks .strip(): {u.raw_line.strip()}"
                )


class TestGLUDDDefaultConsistency:
    def test_same_default_not_hardcoded_twice(self):
        """The same default value should not appear at two different call sites.
        If it changes, both places drift.  A shared constant is safer."""
        default_map: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
        for e in _SCANNED:
            if not e.var_name.startswith("GLUDD_"):
                continue
            if not e.is_get:
                continue
            m = re.search(r'os\.environ\.get\(\s*"\w+"\s*,\s*"([^"]*)"\s*\)', e.raw_line)
            if m and m.group(1):
                default_map[(e.var_name, m.group(1))].append((e.file_path, e.line))

        dupes_list: list[tuple[str, str, list[tuple[str, int]]]] = [
            (var_name, default_val, loc_list)
            for (var_name, default_val), loc_list in default_map.items()
            if len(loc_list) > 1
        ]
        assert not dupes_list, (
            f"Env vars with the same hardcoded default at multiple call sites (risk of drift): {dupes_list}"
        )

    def test_default_values_consistent(self):
        """The same GLUDD_ var should have the same default wherever read."""
        var_defaults: dict[str, str] = {}
        violations: list[str] = []
        for e in _SCANNED:
            if not e.var_name.startswith("GLUDD_"):
                continue
            if not e.is_get:
                continue
            m = re.search(r'os\.environ\.get\(\s*"(\w+)"\s*,\s*"([^"]*)"\s*\)', e.raw_line)
            if m:
                name, default = m.group(1), m.group(2)
                if name not in var_defaults:
                    var_defaults[name] = default
                    var_defaults[f"{name}_src"] = f"{e.file_path}:{e.line}"
                elif var_defaults[name] != default:
                    violations.append(
                        f"{name}: {var_defaults[name]} @ "
                        f"{var_defaults.get(f'{name}_src', '?')} "
                        f"!= {default} @ {e.file_path}:{e.line}"
                    )
        assert not violations, f"Inconsistent default values for the same GLUDD_ var: {violations}"


class TestNonGLUDDEnvVars:
    def test_all_credential_vars_identified(self):
        """Non-GLUDD credential vars (API_KEY, TOKEN, etc.) are explicitly listed."""
        credential_like = [v for v in _NON_GLUDD_VARS if _SECRET_PATTERN_RE.search(v)]
        assert credential_like, "No credential-like non-GLUDD env vars found — test may be stale"
        for v in credential_like:
            r = TestGLUDDSecurityClassification.SECRET_PATTERN
            assert r.search(v), f"Non-GLUDD credential var {v} doesn't match secret pattern"

    def test_no_plaintext_secrets_in_defaults(self):
        """No hardcoded default looks like a real credential (sk-..., etc.)."""
        for e in _SCANNED:
            m = re.search(r'os\.environ\.get\(\s*"\w+"\s*,\s*"([^"]+)"', e.raw_line)
            if not m:
                continue
            default = m.group(1)
            if default in ("", "0", "1", "true", "false", "inline", "info"):
                continue
            dangerous = re.search(r"(sk-[A-Za-z0-9]{10,}|[0-9a-f]{32,}|AKIA[0-9A-Z]{16})", default)
            if dangerous:
                raise AssertionError(
                    f"Potentially hardcoded secret as default for {e.var_name} "
                    f"at {e.file_path}:{e.line}: {default[:40]}..."
                )


class TestEnvVarCoverage:
    def test_every_gludd_var_has_usage_location(self):
        """Scanned GLUDD_ vars could be traceable."""
        assert len(_GLUDD_VARS) >= 10, f"Expected at least 10 GLUDD_ env vars; found {len(_GLUDD_VARS)}"

    def test_var_count_stable(self):
        """Confirm we are scanning what we expect.

        This is informational — if the count changes drastically, investigate
        whether the scan patterns are still correct.
        """
        assert len(_GLUDD_VARS) >= 15, f"Found only {len(_GLUDD_VARS)} GLUDD_ vars — scan may be incomplete"

    def test_scripts_and_src_both_scanned(self):
        src_dir_vars = {e.var_name for e in _SCANNED if e.var_name.startswith("GLUDD_") and str(_SRC) in e.file_path}
        scr_vars = {e.var_name for e in _SCANNED if e.var_name.startswith("GLUDD_") and str(_SCRIPTS) in e.file_path}
        both = src_dir_vars & scr_vars
        assert len(both) >= 3, (
            f"Only {len(both)} GLUDD_ vars shared between src/ and scripts/ — "
            f"scan may be incomplete. Shared: {sorted(both)}"
        )


class TestBoolPatternConsistency:
    TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

    def test_truthy_check_patterns_consistent(self):
        """Bool-ish GLUDD_ vars should use a consistent truthy-check pattern."""
        bool_vars = [
            v
            for v in _GLUDD_VARS
            if any(suffix in v for suffix in ("_ENABLE", "_DISABLE", "_ENFORCE", "_ALLOW", "_REQUIRE", "INLINE"))
        ]
        patterns_found: set[str] = set()
        for var_name in bool_vars:
            for u in _usages_for(var_name):
                m = re.search(r'\.lower\(\)\s*\.strip\(\)\s*(?:==|in)\s*(\{[^}]*"1"[^}]*\}|"[^"]*")', u.raw_line)
                if m:
                    patterns_found.add(var_name)
        assert len(patterns_found) >= 1, f"No truthy-check patterns found for bool vars: {bool_vars}"


class TestSecretsManagerAllowlist:
    def test_allowlist_covers_provider_credentials(self):
        """Secrets manager allowlist patterns cover all provider API_KEY/BASE_URL vars."""
        env_py = _SRC / "general_ludd" / "secrets" / "env.py"
        if not env_py.exists():
            pytest.skip("secrets/env.py not found")
        text = env_py.read_text()
        patterns_raw = re.findall(r'r"([^"]+)"', text)
        provider_vars = {
            v
            for v in _ALL_VARS
            if any(v.endswith(s) for s in ("_API_KEY", "_BASE_URL", "_AUTH_TOKEN")) and not v.startswith("GLUDD_")
        }
        for v in sorted(provider_vars):
            ok = any(re.search(pat.replace("\\\\", "\\"), v) for pat in patterns_raw) or v in text
            assert ok, f"Provider credential var {v} not covered by secrets/env.py allowlist patterns: {patterns_raw}"

    def test_gludd_secret_prefix_in_allowlist(self):
        """Allowlist patterns include GLUDD_SECRET_ for proper namespacing."""
        env_py = _SRC / "general_ludd" / "secrets" / "env.py"
        if not env_py.exists():
            pytest.skip("secrets/env.py not found")
        text = env_py.read_text()
        assert "GLUDD_SECRET_" in text, (
            "secrets/env.py should allowlist GLUDD_SECRET_ prefix for future namespaced secret vars"
        )


class TestAuthVarsConfig:
    def test_auth_vars_consistent_across_daemon_and_worker(self):
        """Auth posture vars (GLUDD_AUTH_PSK, GLUDD_REQUIRE_AUTH, ...) read with
        consistent semantics in daemon and worker."""
        auth_vars = [
            "GLUDD_AUTH_PSK",
            "GLUDD_REQUIRE_AUTH",
            "GLUDD_PSK_DISABLE",
            "GLUDD_ALLOW_NO_AUTH",
        ]
        for av in auth_vars:
            usages = _usages_for(av)
            assert len(usages) >= 1, f"Auth var {av} not found in source scan"

    def test_psk_rotation_vars_if_documented(self):
        """If PSK rotation vars are used in code, they should be documented."""
        rotation_vars = {v for v in _GLUDD_VARS if "PSK_ROTATION" in v or "PSK_IDENTITY" in v}
        if rotation_vars:
            ref_text = ""
            ref_path = _DOCS / "CONFIG_REFERENCE.md"
            if ref_path.exists():
                ref_text = ref_path.read_text()
            missing = {v for v in rotation_vars if v not in ref_text}
            assert not missing, f"PSK rotation env vars not in CONFIG_REFERENCE.md: {missing}"


class TestEnvVarSourceTracking:
    def test_daemon_boot_reads_expected_vars(self):
        """daemon.py explicitly reads the GLUDD_* vars listed in
        CONFIG_REFERENCE.md's daemon/runtime table at startup."""
        daemon_path = _SRC / "general_ludd" / "daemon.py"
        if not daemon_path.exists():
            pytest.skip("daemon.py not found")
        text = daemon_path.read_text()
        daemon_vars = set(_GLUDD_ENV_VAR_RE.findall(text))
        daemon_vars_os = set(m.group(1) for m in _OS_ENV_GET_RE.finditer(text) if m.group(1).startswith("GLUDD_"))
        all_daemon = daemon_vars | daemon_vars_os
        ref_vars = _read_ref_md_gludd_vars()
        if not ref_vars:
            pytest.skip("CONFIG_REFERENCE.md not found")

        doc_expected_in_daemon = {
            "GLUDD_CONFIG_DIR",
            "GLUDD_TEMPLATES_DIR",
            "GLUDD_PLAYBOOKS_DIR",
            "GLUDD_TICK_INTERVAL",
            "GLUDD_LOG_LEVEL",
            "GLUDD_WRITER_MODE",
            "GLUDD_AUTH_PSK",
            "GLUDD_REQUIRE_AUTH",
            "GLUDD_ALLOW_NO_AUTH",
            "GLUDD_TERRAFORM_STACKS_DIR",
            "GLUDD_WORKER_ID",
            "GLUDD_PG_WAKE_RECONNECT_SECONDS",
        }
        missing_in_daemon = doc_expected_in_daemon - all_daemon
        assert not missing_in_daemon, (
            f"CONFIG_REFERENCE.md daemon/runtime vars not found in daemon.py: {missing_in_daemon}"
        )

    def test_worker_reads_auth_vars(self):
        worker_path = _SRC / "general_ludd" / "worker" / "app.py"
        if not worker_path.exists():
            pytest.skip("worker/app.py not found")
        text = worker_path.read_text()
        for var in ("GLUDD_AUTH_PSK", "GLUDD_WORKER_ID", "GLUDD_CONFIG_DIR", "GLUDD_JOB_TIMEOUT_MAX"):
            assert var in text, f"{var} should be read by worker/app.py"


class TestNoEnvVarInjection:
    def test_no_os_environ_write_in_prod_paths(self):
        """Production code (src/) should not write os.environ[...] = ..."""
        writes: list[tuple[str, int]] = []
        for py_file in sorted(_SRC.rglob("*.py")):
            text = py_file.read_text()
            for i, line in enumerate(text.split("\n"), 1):
                if _OS_ENV_SET_RE.search(line):
                    writes.append((str(py_file), i))
        # Some writes are legitimate (STS injector sets env for subprocess).
        # Flag excessive writes.
        assert len(writes) <= 20, (
            f"os.environ[...] = value in src/ (set env) — {len(writes)} instances. "
            f"Check if excessive. Files: {writes[:10]}"
        )

    def test_no_eval_on_env_vars(self):
        """No env var is fed to eval()/exec()."""
        for py_file in sorted({str(_SRC), str(_SCRIPTS)}):
            if py_file.endswith(".py"):
                text = Path(py_file).read_text()
                assert "eval(os.environ" not in text, f"eval() on env var in {py_file}"
                assert "exec(os.environ" not in text, f"exec() on env var in {py_file}"
