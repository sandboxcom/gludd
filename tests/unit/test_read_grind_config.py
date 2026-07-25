"""BP.14: Verify read-grind threshold configurability in enforce-delegate.ts.

Every read-grind threshold constant is configurable via a GLUDD_READ_GRIND_*
env var with a sensible default. These tests are STRUCTURAL — they read the
plugin source as text and assert the config contracts hold.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENFORCE_DELEGATE = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"


def _src() -> str:
    return ENFORCE_DELEGATE.read_text()


# -- canonical env var name prefix for read-grind config --
READ_GRIND_ENV_PREFIX = "GLUDD_READ_GRIND_"


def _extract_default(pattern: str, src: str) -> str | None:
    """Extract the default value from a `parseInt(process.env.X || "Y", 10)` pattern."""
    m = re.search(pattern, src)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# Threshold → env var mapping
# --------------------------------------------------------------------------- #
class TestReadGrindEnvVarMapping:
    """Each read-grind threshold constant MUST be configurable via an
    env var whose name begins with GLUDD_READ_GRIND_."""

    def test_read_grind_file_env_var(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_FILE\s*=\s*process\.env\.(GLUDD_READ_GRIND_FILE)',
            src,
        )
        assert m, "READ_GRIND_FILE must read from process.env.GLUDD_READ_GRIND_FILE"
        env_name = m.group(1)
        assert env_name.startswith(READ_GRIND_ENV_PREFIX), (
            f"env var {env_name} must start with {READ_GRIND_ENV_PREFIX}"
        )
        assert env_name == "GLUDD_READ_GRIND_FILE"

    def test_advisory_count_env_var(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_ADVISORY_COUNT\s*=\s*parseInt\s*\(\s*process\.env\.(GLUDD_READ_GRIND_ADVISORY_COUNT)\s*\|\|',
            src,
        )
        assert m, (
            "READ_GRIND_ADVISORY_COUNT must read from "
            "process.env.GLUDD_READ_GRIND_ADVISORY_COUNT"
        )
        env_name = m.group(1)
        assert env_name.startswith(READ_GRIND_ENV_PREFIX), (
            f"env var {env_name} must start with {READ_GRIND_ENV_PREFIX}"
        )

    def test_advisory_ms_env_var(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_ADVISORY_MS\s*=\s*parseInt\s*\(\s*process\.env\.(GLUDD_READ_GRIND_ADVISORY_MS)\s*\|\|',
            src,
        )
        assert m, (
            "READ_GRIND_ADVISORY_MS must read from "
            "process.env.GLUDD_READ_GRIND_ADVISORY_MS"
        )
        env_name = m.group(1)
        assert env_name.startswith(READ_GRIND_ENV_PREFIX), (
            f"env var {env_name} must start with {READ_GRIND_ENV_PREFIX}"
        )

    def test_deny_count_env_var(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_DENY_COUNT\s*=\s*parseInt\s*\(\s*process\.env\.(GLUDD_READ_GRIND_DENY_COUNT)\s*\|\|',
            src,
        )
        assert m, (
            "READ_GRIND_DENY_COUNT must read from "
            "process.env.GLUDD_READ_GRIND_DENY_COUNT"
        )
        env_name = m.group(1)
        assert env_name.startswith(READ_GRIND_ENV_PREFIX), (
            f"env var {env_name} must start with {READ_GRIND_ENV_PREFIX}"
        )

    def test_deny_ms_env_var(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_DENY_MS\s*=\s*parseInt\s*\(\s*process\.env\.(GLUDD_READ_GRIND_DENY_MS)\s*\|\|',
            src,
        )
        assert m, (
            "READ_GRIND_DENY_MS must read from "
            "process.env.GLUDD_READ_GRIND_DENY_MS"
        )
        env_name = m.group(1)
        assert env_name.startswith(READ_GRIND_ENV_PREFIX), (
            f"env var {env_name} must start with {READ_GRIND_ENV_PREFIX}"
        )

    def test_stale_ms_env_var(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_STALE_MS\s*=\s*parseFloat\s*\(\s*process\.env\.(GLUDD_READ_GRIND_STALE_MS)\s*\|\|',
            src,
        )
        assert m, (
            "READ_GRIND_STALE_MS must read from "
            "process.env.GLUDD_READ_GRIND_STALE_MS"
        )
        env_name = m.group(1)
        assert env_name.startswith(READ_GRIND_ENV_PREFIX), (
            f"env var {env_name} must start with {READ_GRIND_ENV_PREFIX}"
        )

    def test_all_env_vars_have_gludd_read_grind_prefix(self):
        """Every env var in the read-grind section must use the GLUDD_READ_GRIND_* prefix."""
        src = _src()
        # Find all process.env references near READ_GRIND constants.
        # Look for all env var names between READ_GRIND_FILE and the next
        # major section (DISK_DANGER_GB).
        read_grind_section = src[src.index("READ_GRIND_FILE") : src.index("DISK_DANGER_GB")]
        env_refs = re.findall(r"process\.env\.(\w+)", read_grind_section)
        for name in env_refs:
            assert name.startswith(READ_GRIND_ENV_PREFIX), (
                f"env var {name} in read-grind section must start with "
                f"{READ_GRIND_ENV_PREFIX}"
            )
        assert len(env_refs) >= 6, (
            f"Expected at least 6 read-grind env var references, got {len(env_refs)}"
        )


# --------------------------------------------------------------------------- #
# Default value sanity
# --------------------------------------------------------------------------- #
class TestReadGrindDefaultValues:
    """Default values for read-grind thresholds must be sensible positive
    integers with the correct ordering (advisory < deny)."""

    def test_advisory_count_default_is_positive(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_ADVISORY_COUNT\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"',
            src,
        )
        assert m, "READ_GRIND_ADVISORY_COUNT default not found"
        default = int(m.group(1))
        assert default > 0, f"Advisory count default ({default}) must be positive"

    def test_advisory_ms_default_is_positive(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_ADVISORY_MS\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"',
            src,
        )
        assert m, "READ_GRIND_ADVISORY_MS default not found"
        default = int(m.group(1))
        assert default >= 10_000, (
            f"Advisory ms default ({default}) must be >= 10s (10000ms)"
        )

    def test_deny_count_default_is_positive(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_DENY_COUNT\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"',
            src,
        )
        assert m, "READ_GRIND_DENY_COUNT default not found"
        default = int(m.group(1))
        assert default > 0, f"Deny count default ({default}) must be positive"

    def test_deny_ms_default_is_positive(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_DENY_MS\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"',
            src,
        )
        assert m, "READ_GRIND_DENY_MS default not found"
        default = int(m.group(1))
        assert default >= 30_000, (
            f"Deny ms default ({default}) must be >= 30s (30000ms)"
        )

    def test_stale_ms_default_is_positive(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_STALE_MS\s*=\s*parseFloat\s*\([^|]+\|\|\s*"(\d+)"',
            src,
        )
        assert m, "READ_GRIND_STALE_MS default not found"
        default = float(m.group(1))
        assert default >= 30_000, (
            f"Stale ms default ({default}) must be >= 30s (30000ms)"
        )

    def test_advisory_count_less_than_deny_count(self):
        """Advisory must fire BEFORE deny — advisory_count < deny_count."""
        src = _src()
        m_adv = re.search(
            r'READ_GRIND_ADVISORY_COUNT\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"',
            src,
        )
        m_deny = re.search(
            r'READ_GRIND_DENY_COUNT\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"',
            src,
        )
        assert m_adv and m_deny, "Could not extract advisory/deny count defaults"
        adv_count = int(m_adv.group(1))
        deny_count = int(m_deny.group(1))
        assert adv_count < deny_count, (
            f"Advisory count ({adv_count}) must be less than deny count ({deny_count})"
        )

    def test_advisory_ms_less_than_deny_ms(self):
        """Advisory time window must be shorter than deny time window."""
        src = _src()
        m_adv = re.search(
            r'READ_GRIND_ADVISORY_MS\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"',
            src,
        )
        m_deny = re.search(
            r'READ_GRIND_DENY_MS\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"',
            src,
        )
        assert m_adv and m_deny, "Could not extract advisory/deny ms defaults"
        adv_ms = int(m_adv.group(1))
        deny_ms = int(m_deny.group(1))
        assert adv_ms < deny_ms, (
            f"Advisory ms ({adv_ms}) must be less than deny ms ({deny_ms})"
        )

    def test_read_grind_file_default_is_tmp_path(self):
        src = _src()
        m = re.search(
            r'READ_GRIND_FILE\s*=\s*process\.env\.[^|]+\|\|\s*"([^"]+)"',
            src,
        )
        assert m, "READ_GRIND_FILE default not found"
        default_path = m.group(1)
        assert default_path.startswith("/tmp/gludd-"), (
            f"READ_GRIND_FILE default ({default_path}) must be under /tmp/gludd-*"
        )


# --------------------------------------------------------------------------- #
# Config contract consistency
# --------------------------------------------------------------------------- #
class TestReadGrindConfigConsistency:
    """Beyond the individual values: the overall config pattern must be consistent."""

    def test_every_read_grind_constant_has_env_var(self):
        """Every READ_GRIND_* constant declaration must reference process.env."""
        src = _src()
        read_grind_section = src[src.index("READ_GRIND_FILE") : src.index("DISK_DANGER_GB")]
        # Find all READ_GRIND_* constant declarations
        constants = re.findall(r"(READ_GRIND_\w+)", read_grind_section)
        assert len(constants) >= 6, (
            f"Expected at least 6 READ_GRIND_* constants, got {len(constants)}: {constants}"
        )
        # Each READ_GRIND_* constant line must include process.env
        for line in read_grind_section.split("\n"):
            if re.match(r"const\s+READ_GRIND_\w+\s*=", line.strip()):
                assert "process.env" in line, (
                    f"READ_GRIND_* constant must reference process.env: {line.strip()}"
                )

    def test_count_env_vars_use_parseint(self):
        """Count thresholds must use parseInt() for integer coercion."""
        src = _src()
        for const_name in ["READ_GRIND_ADVISORY_COUNT", "READ_GRIND_DENY_COUNT"]:
            pattern = rf'{const_name}\s*=\s*parseInt\s*\('
            assert re.search(pattern, src), (
                f"{const_name} must use parseInt() for coercion"
            )

    def test_ms_env_vars_use_parseint_or_parsefloat(self):
        """Time thresholds must use parseInt() or parseFloat() for coercion."""
        src = _src()
        for const_name in ["READ_GRIND_ADVISORY_MS", "READ_GRIND_DENY_MS",
                           "READ_GRIND_STALE_MS"]:
            pattern = rf'{const_name}\s*=\s*(?:parseInt|parseFloat)\s*\('
            assert re.search(pattern, src), (
                f"{const_name} must use parseInt() or parseFloat() for coercion"
            )

    def test_no_hardcoded_read_grind_literal_in_enforcement(self):
        """The read-grind enforcement logic must use the named constants
        (READ_GRIND_ADVISORY_COUNT etc.), NOT inline numeric literals
        for thresholds. This proves the configurability is wired through."""
        src = _src()
        # The enforcement logic in mainthreadBudgetBefore must reference
        # READ_GRIND_DENY_COUNT and READ_GRIND_ADVISORY_COUNT — not inline
        # literals like `rs.count > 10` or `rs.count > 5`.
        budget_fn = src[src.index("mainthreadBudgetBefore") : src.index("mainthreadBudgetAfter")]
        assert "READ_GRIND_DENY_COUNT" in budget_fn, (
            "mainthreadBudgetBefore must use READ_GRIND_DENY_COUNT constant "
            "(not an inline literal) so env var config takes effect"
        )
        assert "READ_GRIND_ADVISORY_COUNT" in budget_fn or \
               "READ_GRIND_ADVISORY_MS" in budget_fn, (
            "mainthreadBudgetBefore must use READ_GRIND_ADVISORY_* constants "
            "(not inline literals) so env var config takes effect"
        )
        assert "READ_GRIND_DENY_MS" in budget_fn, (
            "mainthreadBudgetBefore must use READ_GRIND_DENY_MS constant"
        )


# --------------------------------------------------------------------------- #
# AGENTS.md documentation
# --------------------------------------------------------------------------- #
class TestReadGrindAgentsMdDocumentation:
    """The read-grind env vars must be documented in AGENTS.md."""

    def test_agents_md_mentions_read_grind_config(self):
        agents_md = (ROOT / "AGENTS.md").read_text()
        # At a minimum AGENTS.md should reference read-grinding and the
        # env-var configurability pattern.
        assert "READ_GRIND" in agents_md.upper(), (
            "AGENTS.md must mention READ-GRIND thresholds"
        )
        assert "READ-GRIND" in agents_md or "READ_GRIND" in agents_md, (
            "AGENTS.md must contain READ_GRIND or READ-GRIND references"
        )

    def test_default_values_match_source(self):
        """The default values documented inline in enforce-delegate.ts
        (comments on lines 70-78) must match the literal defaults."""
        src = _src()
        # Extract the documented defaults from comments
        doc_adv_count = re.search(r"ADVISORY:\s*>\s*(\d+)\s*calls", src)
        doc_deny_count = re.search(r"BLOCK:\s*>\s*(\d+)\s*calls", src)
        # Extract the actual code defaults
        code_adv_count = re.search(
            r'READ_GRIND_ADVISORY_COUNT\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"', src)
        code_deny_count = re.search(
            r'READ_GRIND_DENY_COUNT\s*=\s*parseInt\s*\([^|]+\|\|\s*"(\d+)"', src)
        if doc_adv_count and code_adv_count:
            assert doc_adv_count.group(1) == code_adv_count.group(1), (
                f"Doc advisory count ({doc_adv_count.group(1)}) must match "
                f"code default ({code_adv_count.group(1)})"
            )
        if doc_deny_count and code_deny_count:
            assert doc_deny_count.group(1) == code_deny_count.group(1), (
                f"Doc deny count ({doc_deny_count.group(1)}) must match "
                f"code default ({code_deny_count.group(1)})"
            )
