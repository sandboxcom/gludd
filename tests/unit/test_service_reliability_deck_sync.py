"""Pin the service-discovery and lifecycle reliability story in the deck."""

from __future__ import annotations

from pathlib import Path

from scripts import build_deck

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
SERVICE_DISCOVERY_CONTRACT = (
    ROOT / "docs/features/SERVICE_DISCOVERY_SEARCH_TERM_SCHEMA.md"
)


def _reliability_slide() -> str:
    """Return the one slide that composes the four reliability contracts."""
    content = DECK.read_text(encoding="utf-8")
    return content.split('<section data-contract="service-reliability-boundaries">', 1)[
        1
    ].split("</section>", 1)[0]


def test_reveal_deck_composes_the_four_reliability_boundaries_in_order() -> None:
    """The deck retains the fail-closed path from input through shutdown."""
    slide = _reliability_slide()

    ordered_markers = (
        "Validate before I/O",
        "Isolate every test",
        "Choose lifecycle explicitly",
        "Keep shutdown observable",
    )
    positions = [slide.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)

    for marker in (
        "(identifier, query)",
        "plain strings stay compatible",
        "empty results preserve the catalog",
        "project-scoped catalog",
        "health-socket state",
        "monkeypatch",
        "PID + counter",
        "pre-startup",
        "/readyz",
        "context-managed startup",
        "per-app in-memory database",
        "closed log stream",
        "stderr file descriptor 2",
        "restore handlers + propagation",
        "S83.112",
        "publish its matching receipt before exec",
        "serial base or real parallel fragments",
        "S83.113",
        "prefix-scoped import probe",
        "external dependencies cached",
        "docs/features/SERVICE_DISCOVERY_SEARCH_TERM_SCHEMA.md",
        "docs/features/FLOOR_TEST_ENV_ISOLATION.md",
        "docs/features/READINESS_LIFESPAN_TESTING.md",
        "docs/features/RESOURCE_LIFECYCLE_SHUTDOWN_LOGGING.md",
    ):
        assert marker in slide


def test_gate_and_import_boundaries_have_exact_local_and_github_links() -> None:
    """New reliability evidence must open at the cited repository lines."""
    deck = DECK.read_text(encoding="utf-8")
    sha = "f" * 40
    linked, citations = build_deck.link_source_citations(deck, sha)
    slide = linked.split(
        '<section data-contract="service-reliability-boundaries">', 1
    )[1].split("</section>", 1)[0]
    expected = {
        "docs/features/GATE_RESOURCE_LIFECYCLE.md": (
            "577-608",
            "1102-1129",
        ),
        "docs/features/QEMU_IMPORT_ISOLATION.md": ("18-43",),
    }

    for path, ranges in expected.items():
        assert path in citations
        for lines in ranges:
            start, end = lines.split("-", 1)
            assert f'data-source-path="{path}"' in slide
            assert f'data-source-lines="{lines}"' in slide
            assert (
                f"https://github.com/sandboxcom/gludd/blob/{sha}/{path}"
                f"#L{start}-L{end}"
            ) in slide


def test_reveal_deck_retains_prior_decisions_and_live_tokens() -> None:
    """The reliability insertion cannot displace decisions or build provenance."""
    deck = DECK.read_text(encoding="utf-8")

    assert deck.count('data-contract="s83-157-next-live-proof"') == 1
    assert deck.count('data-contract="s83-163-frozen-delta-hold"') == 1
    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in deck


def test_service_discovery_contract_retains_namespace_isolation_evidence() -> None:
    """Canonical docs retain the boundary and the practitioner evidence."""
    contract = SERVICE_DISCOVERY_CONTRACT.read_text(encoding="utf-8")

    for marker in (
        "project_namespace",
        "health-socket state",
        "hashicorp/consul/issues/5842",
        "pytest-dev/pytest-xdist/issues/524",
    ):
        assert marker in contract
