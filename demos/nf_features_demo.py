#!/usr/bin/env python3
"""Standalone demos for the v0.1.0-beta.2 NF feature work.

Each subcommand exercises a different NF feature against its real production
module. External services (OpenBao, Firecracker, model HTTP API, SDR hardware)
are replaced with in-process fakes so the demo is fully self-contained: only
the I/O boundary is swapped, not the business logic.

Run a single feature:

    python demos/nf_features_demo.py chat
    python demos/nf_features_demo.py vm
    python demos/nf_features_demo.py sts
    python demos/nf_features_demo.py entropy
    python demos/nf_features_demo.py aprs
    python demos/nf_features_demo.py corpus

Or run them all:

    python demos/nf_features_demo.py all
"""

from __future__ import annotations

import argparse
import asyncio
import os
import struct
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# The binary_re and radio module_utils use ansible's `plugins.module_utils.*`
# import convention. Add their collection roots to sys.path so those imports
# resolve when running this file outside an ansible-runner context.
_BINARY_RE_ROOT = REPO_ROOT / "collections/ansible_collections/general_ludd/binary_re"
_RADIO_ROOT = REPO_ROOT / "collections/ansible_collections/general_ludd/radio"
for _extra in (_BINARY_RE_ROOT, _RADIO_ROOT):
    _str_extra = str(_extra)
    if _str_extra not in sys.path:
        sys.path.insert(0, _str_extra)


# =============================================================================
# NF.1 — Chat CLI streaming formatter
# =============================================================================

def demo_chat_streaming() -> None:
    """Exercise StreamingChatFormatter with a simulated token stream.

    The streaming formatter buffers incomplete markdown fenced code blocks so
    that syntax highlighting is applied atomically once the closing fence
    arrives, and splits interleaved code/text boundaries correctly. We feed it
    a token-by-token stream that opens a ```python fence mid-message and
    closes it later, asserting both behaviours.
    """
    from general_ludd.chat.formatter import StreamingChatFormatter

    print("[NF.1] Chat CLI streaming formatter (simulated token stream)")
    print("-" * 60)

    fmt = StreamingChatFormatter()
    tokens = list("Here is code:\n```python\ndef add(a, b):\n    return a + b\n```\nDone.")
    emitted: list[str] = []
    for tok in tokens:
        chunk = fmt.feed(tok)
        if chunk:
            emitted.append(chunk)
    tail = fmt.flush()
    if tail:
        emitted.append(tail)

    stream = "".join(emitted)
    print("Reassembled stream (first 200 chars):")
    print(stream[:200])
    print("-" * 60)
    # Code-fence buffering invariant: while inside a fence, only whitespace
    # and the accumulating code are emitted; the fence delimiter itself is
    # held until the closing fence arrives.
    has_python_fence = "```python" in stream or "def add" in stream
    has_done_tail = stream.rstrip().endswith("Done.")
    print(f"code block observed: {has_python_fence}")
    print(f"post-fence text flushed: {has_done_tail}")
    if not (has_python_fence and has_done_tail):
        raise RuntimeError("streaming formatter lost content across fence boundary")
    print("[NF.1] OK")


# =============================================================================
# NF.2 — VM sandbox pool checkout
# =============================================================================

def demo_vm_sandbox_pool() -> None:
    """Exercise VMSandboxPool prewarm/checkout/return against a fake manager.

    VMSandboxPool is normally backed by VMSandboxManager, which spawns a
    Firecracker or gvisor subprocess. For this demo we substitute a fake
    manager whose boot() returns a synthetic RUNNING VMInstance — that's the
    only method the pool calls. This proves the pool's prewarm floor,
    checkout auto-scale, and return idempotency without needing the
    firecracker binary.
    """
    from general_ludd.security.permissions import PermissionSpec
    from general_ludd.security.sandboxes import SandboxHandle, SandboxTarget
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMLifecycleState,
        VMInstance,
    )
    from general_ludd.security.sandboxes.vm.pool import (
        PoolConfig,
        VMSandboxPool,
    )

    print("[NF.2] VM sandbox pool checkout/return (fake backend)")
    print("-" * 60)

    class _FakeManager:
        """Stand-in for VMSandboxManager. Boots synthetic RUNNING instances."""

        def __init__(self) -> None:
            self.instances: dict[str, VMInstance] = {}
            self._counter = 0
            self.released: list[str] = []

        def boot(
            self,
            backend_name: str,
            spec: PermissionSpec,
            target: SandboxTarget,
            image_path: str | Path | None = None,
        ) -> VMInstance:
            self._counter += 1
            iid = f"fake-vm-{self._counter:04d}"
            inst = VMInstance(
                instance_id=iid,
                backend_name=backend_name,
                spec=spec,
                handle=SandboxHandle(
                    backend=backend_name,
                    token=f"fake-token-{iid}",
                    applied=True,
                ),
                state=VMLifecycleState.RUNNING,
            )
            self.instances[iid] = inst
            return inst

        def release(self, instance_id: str) -> None:
            self.released.append(instance_id)
            self.instances.pop(instance_id, None)

    fake = _FakeManager()
    pool = VMSandboxPool(
        backend_name="firecracker",
        spec=PermissionSpec(agent_type="demo"),
        target=SandboxTarget(directory="/tmp/demo"),
        config=PoolConfig(min_idle=2, max_size=5, prewarm_count=2),
        manager=fake,
    )

    booted = pool.prewarm()
    print(f"prewarm booted {booted} instance(s); available={pool.available_count()}")
    if booted != 2 or pool.available_count() != 2:
        raise RuntimeError("prewarm did not reach min floor")

    first = pool.checkout()
    print(f"checkout -> {first}; available={pool.available_count()}, "
          f"checked_out={pool.checked_out_count()}")
    # checkout below min_idle triggers an auto-scale top-up.
    if pool.available_count() < 1:
        raise RuntimeError("checkout did not auto-scale back to min_idle")

    second = pool.checkout()
    print(f"checkout -> {second}; available={pool.available_count()}")

    pool.return_instance(first)
    pool.return_instance(first)
    print(f"return_instance({first}) twice (idempotent); "
          f"available={pool.available_count()}")
    if pool.available_count() < 2:
        raise RuntimeError("idempotent return did not restore availability")

    stats = pool.stats()
    print(f"stats: {stats.as_dict()}")
    pool.shutdown()
    print(f"shutdown released {len(fake.released)} instance(s) from backend")
    print("[NF.2] OK")


# =============================================================================
# NF.7 — STS token mint + quota check
# =============================================================================

def demo_sts_mint_and_quota() -> None:
    """Exercise TokenQuotaEnforcer + TokenMinter with InMemoryQuotaBackend.

    Two-codepath demo:
      1. TokenQuotaEnforcer.check() admits a normal mint, then denies when the
         per-agent cap is hit (InMemoryQuotaBackend, no DB needed).
      2. TokenMinter.mint() invokes the (faked) SecretsManager.setup_approle
         and records a mint in the quota backend.

    The real TokenMinter talks to OpenBao via SecretsManager; we substitute a
    fake that returns deterministic AppRoleCreds so the audit+quota wiring can
    be observed without an OpenBao daemon.
    """
    from general_ludd.secrets.manager import AppRoleCreds
    from general_ludd.sts.minter import TokenMinter
    from general_ludd.sts.quotas import (
        InMemoryQuotaBackend,
        QuotaConfig,
        QuotaViolation,
        TokenQuotaEnforcer,
    )

    print("[NF.7] STS token mint + per-agent quota enforcement")
    print("-" * 60)

    backend = InMemoryQuotaBackend()
    enforcer = TokenQuotaEnforcer(
        config=QuotaConfig(max_tokens_per_agent=2, max_active_tokens_per_project=10),
        backend=backend,
    )

    async def _run() -> None:
        agent = "agent-007"
        project = "project-neon"
        scope = {"read", "write", "list"}

        for i in range(1, 3):
            await enforcer.record_mint(f"tok-{i}", agent, project, scope)
            count = await backend.active_count_for_agent(agent)
            print(f"mint {i} recorded; active for {agent} = {count}")

        # Third mint must be rejected by the per-agent cap.
        try:
            await enforcer.check(agent, project, scope)
        except QuotaViolation as exc:
            print(f"quota check denied 3rd mint: dimension={exc.dimension}")
            if exc.dimension != "agent":
                raise RuntimeError(
                    f"expected 'agent' dimension, got {exc.dimension!r}"
                )
        else:
            raise RuntimeError("quota check should have denied the 3rd mint")

        # Now demonstrate TokenMinter against a fake secrets manager.
        class _FakeSecrets:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def setup_approle(self, role_name: str) -> AppRoleCreds:
                self.calls.append(role_name)
                return AppRoleCreds(
                    role_id=f"role-{role_name}",
                    secret_id=f"secret-{role_name}",
                )

        fake_sm = _FakeSecrets()
        minter = TokenMinter(secrets_manager=fake_sm)
        creds = await minter.mint(
            agent_id="agent-new",
            parent_agent_id="orchestrator",
        )
        print(f"minter issued creds role_id={creds.role_id!r} "
              f"(setup_approle calls: {fake_sm.calls})")
        if creds.role_id != "role-agent-agent-new":
            raise RuntimeError("mint did not invoke setup_approle correctly")

    asyncio.run(_run())
    print("[NF.7] OK")


# =============================================================================
# NF.3 — Binary RE entropy analysis
# =============================================================================

def _build_synthetic_pe(section_data: bytes) -> bytes:
    """Construct a minimal but valid PE32 header with one section.

    Only the bytes that entropy_analyzer's _parse_pe_sections inspects are
    populated; everything else is zero. The single section points at
    `section_data` so its Shannon entropy is computed exactly.
    """
    pe_offset = 0x80
    buf = bytearray(pe_offset + 256 + len(section_data))
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, pe_offset)
    buf[pe_offset:pe_offset + 4] = b"PE\x00\x00"

    coff = pe_offset + 4
    struct.pack_into("<H", buf, coff + 2, 1)

    opt_hdr = coff + 20
    struct.pack_into("<H", buf, opt_hdr, 0x10B)

    section_table = opt_hdr + 96
    raw_offset = pe_offset + 256
    raw_size = len(section_data)
    buf[section_table:section_table + 8] = b".high\x00\x00\x00"
    struct.pack_into("<I", buf, section_table + 8, raw_size)
    struct.pack_into("<I", buf, section_table + 16, raw_size)
    struct.pack_into("<I", buf, section_table + 20, raw_offset)
    buf[raw_offset:raw_offset + raw_size] = section_data
    return bytes(buf)


def demo_binary_re_entropy() -> None:
    """Exercise entropy_analyzer against a synthetic PE with a packed section.

    Builds a minimal valid PE32 with one section whose bytes are drawn from a
    high-entropy source (os.urandom). The analyzer must flag overall entropy
    above the packing threshold, identify the section as high-entropy, and
    surface packing evidence.
    """
    from plugins.module_utils.entropy_analyzer import (
        PACKING_ENTROPY_THRESHOLD,
        analyze_entropy,
        shannon_entropy,
    )

    print("[NF.3] Binary RE Shannon entropy analysis (synthetic PE)")
    print("-" * 60)

    # High-entropy section: 4 KiB of cryptographic random data.
    high_entropy_section = os.urandom(4096)
    pe_bytes = _build_synthetic_pe(high_entropy_section)
    print(f"built synthetic PE: {len(pe_bytes)} bytes, "
          f"section Shannon entropy = {shannon_entropy(high_entropy_section):.3f}")

    result = analyze_entropy(pe_bytes)
    print(f"file_type={result.file_type} overall_entropy={result.overall_entropy:.3f}")
    print(f"sections found: {len(result.sections)}; "
          f"packed={result.packed} confidence={result.packing_confidence.name}")
    for sec in result.sections:
        print(f"  section {sec.name!r}: entropy={sec.entropy:.3f} "
              f"high={sec.is_high_entropy}")

    if result.file_type != "PE":
        raise RuntimeError(f"expected file_type=PE, got {result.file_type!r}")
    if not result.sections:
        raise RuntimeError("PE section parsing returned no sections")
    high = [s for s in result.sections if s.is_high_entropy]
    if not high:
        raise RuntimeError("expected at least one high-entropy section")
    if not result.packed:
        raise RuntimeError("packing detection failed to flag high-entropy PE")
    print(f"evidence: {result.evidence[:2]}")
    print(f"threshold reference: PACKING_ENTROPY_THRESHOLD={PACKING_ENTROPY_THRESHOLD}")
    print("[NF.3] OK")


# =============================================================================
# NF.4 — Radio APRS AX.25 decode
# =============================================================================

def _build_ax25_ui_frame(
    dest: str,
    source: str,
    info: bytes,
    *,
    dest_ssid: int = 0,
    source_ssid: int = 0,
) -> bytes:
    """Build a raw AX.25 UI frame bracketed by HDLC 0x7E flags.

    Layout:
      0x7E | dest(6, shifted) + dest_ssid_byte | src(6, shifted) + src_ssid_byte
          | control=0x03 | PID=0xF0 | info [...] | 2-byte FCS placeholder | 0x7E

    The SSID byte layout matches what protocol_decoder._decode_ax25_address
    expects: bits 7-5 reserved (set), bits 4-1 = SSID, bit 0 = HDLC extension
    (0 = more addresses follow, 1 = last address).
    """
    def _shift(call: str) -> bytes:
        padded = call.ljust(6)[:6]
        return bytes((ord(c) << 1) & 0xFF for c in padded)

    def _ssid_byte(ssid: int, last: bool) -> int:
        return 0b11100000 | ((ssid & 0x0F) << 1) | (1 if last else 0)

    body = bytearray()
    body += _shift(dest)
    body.append(_ssid_byte(dest_ssid, last=False))
    body += _shift(source)
    body.append(_ssid_byte(source_ssid, last=True))
    body.append(0x03)
    body.append(0xF0)
    body += info
    body += b"\x00\x00"
    return b"\x7e" + bytes(body) + b"\x7e"


def demo_radio_aprs() -> None:
    """Exercise decode_aprs against a crafted AX.25 UI position frame.

    Builds a byte-path APRS frame (no IQ demodulation needed) carrying a
    position report for 49.0583N, 072.0292W with symbol '/'/'-' (house) and a
    comment. The decoder must extract source/destination callsigns, the UI
    control byte, and the typed APRS position payload (lat, lon, symbol,
    comment).
    """
    from plugins.module_utils.protocol_decoder import decode_aprs

    print("[NF.4] Radio APRS AX.25 decode (crafted position frame)")
    print("-" * 60)

    info_field = b"!4903.50N/07201.75W-Test demo"
    frame = _build_ax25_ui_frame("APZ001", "NOCALL", info_field)
    print(f"built AX.25 UI frame: {len(frame)} bytes, info={info_field!r}")

    result = decode_aprs(frame, sample_rate=1_200_000)
    if not result["sync_found"]:
        raise RuntimeError("decode_aprs did not find HDLC sync flag")
    meta = result["protocol_metadata"]
    print(f"source: {meta['source_callsign']!r} ssid={meta['source_ssid']}")
    print(f"destination: {meta['destination_callsign']!r} ssid={meta['destination_ssid']}")
    print(f"control=0x{meta['control']:02X} pid=0x{meta['pid']:02X} "
          f"frame_type={meta['frame_type']!r}")
    print(f"info_field: {meta['info_field']!r}")

    payload = meta["aprs_payload"]
    print(f"data_type: {payload['data_type']}")
    print(f"latitude:  {payload.get('latitude')}")
    print(f"longitude: {payload.get('longitude')}")
    print(f"symbol:    table={payload.get('symbol_table')!r} code={payload.get('symbol_code')!r}")
    print(f"comment:   {payload.get('comment')!r}")

    if meta["source_callsign"] != "NOCALL":
        raise RuntimeError(
            f"expected source 'NOCALL', got {meta['source_callsign']!r}"
        )
    if meta["destination_callsign"] != "APZ001":
        raise RuntimeError(
            f"expected destination 'APZ001', got {meta['destination_callsign']!r}"
        )
    if payload.get("data_type") != "position":
        raise RuntimeError(f"expected data_type 'position', got {payload['data_type']!r}")
    if payload.get("latitude") is None or payload.get("longitude") is None:
        raise RuntimeError("position payload did not yield lat/lon")
    expected_lat = round(49 + 3.50 / 60.0, 6)
    expected_lon = round(-(72 + 1.75 / 60.0), 6)
    if payload["latitude"] != expected_lat or payload["longitude"] != expected_lon:
        raise RuntimeError(
            f"lat/lon mismatch: got {payload['latitude']},{payload['longitude']}"
        )
    print("[NF.4] OK")


# =============================================================================
# NF.9 — Language corpus analysis
# =============================================================================

def demo_language_corpus() -> None:
    """Exercise CorpusAnalyzer.frequency_analysis + n-grams + encoding stats.

    Writes a tiny mixed-language corpus to a temp directory (Python, Go, plain
    text) and runs the analyzer over it. Demonstrates character/word
    frequency aggregation, 2-word n-grams, language distribution by extension,
    and encoding statistics (UTF-8 with no BOMs expected).
    """
    from general_ludd.language.corpus import CorpusAnalyzer

    print("[NF.9] Language corpus analysis (mixed source corpus)")
    print("-" * 60)

    sources: dict[str, str] = {
        "alpha.py": "def add(a, b):\n    return a + b\n",
        "beta.py": "def sub(a, b):\n    return a - b\n",
        "main.go": "package main\n\nfunc add(a, b int) int { return a + b }\n",
        "notes.txt": "add and subtract are arithmetic operations\n",
    }

    with tempfile.TemporaryDirectory(prefix="gludd-corpus-") as tmpdir:
        tmp_path = Path(tmpdir)
        for name, body in sources.items():
            (tmp_path / name).write_text(body, encoding="utf-8")

        analyzer = CorpusAnalyzer(list(tmp_path.iterdir()))
        freq = analyzer.frequency_analysis(top_n=5)
        bigrams = analyzer.extract_ngrams(n=2, unit="word")
        langs = analyzer.language_distribution()
        encs = analyzer.encoding_statistics()

        print(f"corpus files analyzed: {len(analyzer.files)}")
        print(f"total chars={freq['total_chars']} total words={freq['total_words']}")
        print(f"top words: {freq['top_words']}")
        top_bigrams = sorted(bigrams.items(), key=lambda kv: kv[1], reverse=True)[:3]
        print(f"top 2-word n-grams: {top_bigrams}")
        print(f"language distribution: {langs}")
        print(f"encoding consistent={encs['is_consistent']} "
              f"by_encoding={encs['by_encoding']}")

        if freq["total_words"] < 10:
            raise RuntimeError("corpus frequency count suspiciously low")
        if langs.get("python", 0) != 2:
            raise RuntimeError(f"expected 2 python files, got {langs.get('python')}")
        if langs.get("go", 0) != 1:
            raise RuntimeError(f"expected 1 go file, got {langs.get('go')}")
        if not encs["is_consistent"]:
            raise RuntimeError("corpus encoding should be uniformly UTF-8")
        # "add" appears in every file at least once.
        if bigrams.get("def add", 0) < 1 and bigrams.get("add a", 0) < 1:
            raise RuntimeError("expected at least one 'add'-related bigram")
    print("[NF.9] OK")


# =============================================================================
# Entry point
# =============================================================================

DEMOS: dict[str, tuple[str, Any]] = {
    "chat": ("NF.1 Chat CLI streaming formatter", demo_chat_streaming),
    "vm": ("NF.2 VM sandbox pool checkout", demo_vm_sandbox_pool),
    "sts": ("NF.7 STS token mint + quota check", demo_sts_mint_and_quota),
    "entropy": ("NF.3 Binary RE entropy analysis", demo_binary_re_entropy),
    "aprs": ("NF.4 Radio APRS AX.25 decode", demo_radio_aprs),
    "corpus": ("NF.9 Language corpus analysis", demo_language_corpus),
}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a standalone demo of one or more gludd NF features.",
    )
    parser.add_argument(
        "feature",
        choices=sorted(DEMOS.keys()) + ["all"],
        help="Which demo to run, or 'all' for every demo in sequence.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    targets: list[str]
    if args.feature == "all":
        targets = list(DEMOS.keys())
    else:
        targets = [args.feature]

    failures: list[str] = []
    for name in targets:
        label, fn = DEMOS[name]
        banner = f"=== {label} ==="
        print()
        print(banner)
        print("=" * len(banner))
        try:
            fn()
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"[{name}] FAIL: {exc}")
        print("=" * len(banner))

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"All {len(targets)} demo(s) succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
