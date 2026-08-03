"""Verify capability router discovers sandbox collection from
galaxy.yml declarations."""

from __future__ import annotations

import pytest

from general_ludd.dispatch.capabilities import discover_capabilities


class TestSandboxCapabilities:
    @pytest.fixture
    def registry(self):
        return discover_capabilities()

    # ── Sandbox collection discovery ───────────────────────────────────────

    def test_sandbox_collection_discovered(self, registry):
        assert "sandbox" in registry.collections

    def test_sandbox_model_capabilities(self, registry):
        sandbox = registry.collections["sandbox"]
        caps = sandbox.raw_tags
        for cap in (
            "process_sandbox",
            "container_sandbox",
            "firecracker_vm",
            "unikernel_vm",
            "resource_limits",
            "network_policy",
            "security_policy",
            "execution_isolation",
            "backend_routing",
            "capability_routing",
        ):
            assert cap in caps, f"sandbox missing capability tag: {cap}"

    def test_sandbox_tags(self, registry):
        sandbox = registry.collections["sandbox"]
        assert "sandbox" in sandbox.tags
        assert "isolation" in sandbox.tags
        assert "security" in sandbox.tags
        assert "virtualization" in sandbox.tags
        assert "firecracker" in sandbox.tags
        assert "gvisor" in sandbox.tags

    def test_sandbox_backend_tags(self, registry):
        sandbox = registry.collections["sandbox"]
        for backend_tag in (
            "process_sandbox",
            "container_sandbox",
            "firecracker_vm",
            "unikernel_vm",
        ):
            assert backend_tag in sandbox.tags, f"sandbox missing backend tag: {backend_tag}"

    def test_sandbox_security_tags(self, registry):
        sandbox = registry.collections["sandbox"]
        for sec_tag in (
            "resource_limits",
            "network_policy",
            "security_policy",
            "execution_isolation",
            "backend_routing",
        ):
            assert sec_tag in sandbox.tags, f"sandbox missing security tag: {sec_tag}"

    # ── Tag index lookups ─────────────────────────────────────────────────

    def test_sandbox_tag_index_lookups(self, registry):
        for tag in (
            "process_sandbox",
            "container_sandbox",
            "firecracker_vm",
            "unikernel_vm",
            "resource_limits",
            "network_policy",
            "security_policy",
            "execution_isolation",
            "backend_routing",
            "sandbox",
            "isolation",
            "virtualization",
            "firecracker",
            "gvisor",
            "containerization",
            "bubblewrap",
        ):
            matching = registry.lookup_by_tag(tag)
            assert matching, f"no collections found for sandbox tag: {tag}"

    def test_sandbox_capability_tags_in_tag_index(self, registry):
        matching = registry.lookup_by_tag("capability_routing")
        assert matching
        matching_br = registry.lookup_by_tag("backend_routing")
        assert matching_br

    # ── Cross-collection routing ──────────────────────────────────────────

    def test_sandbox_tags_dont_leak_to_radio(self, registry):
        radio = registry.collections["radio"]
        assert "firecracker" not in radio.tags
        assert "process_sandbox" not in radio.tags
        assert "container_sandbox" not in radio.tags
        assert "unikernel_vm" not in radio.tags

    def test_sandbox_tags_dont_leak_to_binary_re(self, registry):
        bre = registry.collections["binary_re"]
        assert "firecracker" not in bre.tags
        assert "process_sandbox" not in bre.tags
        assert "container_sandbox" not in bre.tags
        assert "unikernel_vm" not in bre.tags

    def test_radio_tags_dont_leak_to_sandbox(self, registry):
        sandbox = registry.collections["sandbox"]
        assert "sdr" not in sandbox.tags
        assert "antenna" not in sandbox.tags
        assert "ham" not in sandbox.tags

    def test_binary_re_tags_dont_leak_to_sandbox(self, registry):
        sandbox = registry.collections["sandbox"]
        assert "pe_analyze" not in sandbox.tags
        assert "ghidra" not in sandbox.tags
        assert "fuzz_target" not in sandbox.tags

    def test_cross_collection_no_overlap_with_sandbox(self, registry):
        sandbox_caps = registry.lookup_by_tag("process_sandbox")
        radio_caps = registry.lookup_by_tag("spectrum_scan")
        bre_caps = registry.lookup_by_tag("disassembly")
        assert "radio" not in sandbox_caps
        assert "binary_re" not in sandbox_caps
        assert "sandbox" not in radio_caps
        assert "sandbox" not in bre_caps

    # ── Collection metadata ───────────────────────────────────────────────

    def test_sandbox_collection_namespace(self, registry):
        sandbox = registry.collections["sandbox"]
        assert sandbox.namespace == "general_ludd"
        assert sandbox.version == "0.1.0"

    def test_sandbox_tag_count(self, registry):
        sandbox = registry.collections["sandbox"]
        assert len(sandbox.tags) >= 18, f"expected >=18 tags, got {len(sandbox.tags)}"

    def test_sandbox_raw_tags_no_duplicates(self, registry):
        sandbox = registry.collections["sandbox"]
        assert len(sandbox.raw_tags) == len(set(sandbox.raw_tags)), (
            f"duplicate tags found: {[t for t in sandbox.raw_tags if sandbox.raw_tags.count(t) > 1]}"
        )
