#!/usr/bin/env python3
"""
build_deck.py — Generate docs/presentation/build/index.html as a self-contained
reveal.js deck (reveal.js via CDN, no npm required).

Slides generated:
  1. Title
  2. What gludd is (architecture overview)
  3. Completion status summary (counts from parse_readme_status.py)
  4. One slide per README section (status tables)
  5. Honest competitive comparison (aspirational vs shipped clearly labelled)
  6. Roadmap (what's next, grounded in README's 0% backlog items)
  7. Closing / how to learn more

Honesty rules:
  - All percentage claims come directly from parse_readme_status.py output.
  - Aspirational / not-yet-built items are clearly labelled.
  - No marketing adjectives.

Usage:
  python3 scripts/build_deck.py [--out docs/presentation/build/index.html]
  python3 scripts/build_deck.py --data docs/presentation/deck-data.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Feature data loading
# ---------------------------------------------------------------------------


def _load_features(data_path: str | None) -> dict:
    """Load features from pre-parsed JSON or parse README directly."""
    if data_path and Path(data_path).exists():
        return json.loads(Path(data_path).read_text(encoding="utf-8"))

    # Auto-parse from README
    scripts_dir = Path(__file__).parent
    sys.path.insert(0, str(scripts_dir))
    from parse_readme_status import parse_readme  # type: ignore[import]

    readme = Path(__file__).parent.parent / "README.md"
    features = parse_readme(readme)
    counts = {
        "full": sum(1 for f in features if f["bucket"] == "full"),
        "partial": sum(1 for f in features if f["bucket"] == "partial"),
        "none": sum(1 for f in features if f["bucket"] == "none"),
        "total": len(features),
    }
    return {
        "features": features,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "source": str(readme),
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _pct_bar(pct: int, local_only: bool = False) -> str:
    """Inline progress bar with colour coding."""
    color = "#4caf50" if pct == 100 else "#ff9800" if pct >= 50 else "#f44336"
    label = f"{pct}%"
    if local_only:
        label += " (local)"
        color = "#9c27b0"
    return (
        f'<span class="pct-bar" title="{label}">'
        f'<span class="pct-fill" style="width:{pct}%;background:{color}"></span>'
        f"</span> "
        f'<span class="pct-num">{label}</span>'
    )


def _section_slide(section: str, rows: list[dict]) -> str:
    """One slide per README section showing a table of features and their %."""
    total = len(rows)
    full = sum(1 for r in rows if r["bucket"] == "full")
    partial = sum(1 for r in rows if r["bucket"] == "partial")
    none_count = sum(1 for r in rows if r["bucket"] == "none")

    rows_html_parts = []
    for r in rows:
        tr_class = f'row-{r["bucket"]}'
        title = r["title"]
        if len(title) > 70:
            title = title[:67] + "..."
        pct_html = _pct_bar(r["pct"], r.get("local_only", False))
        rows_html_parts.append(
            f'<tr class="{tr_class}"><td>{title}</td><td>{pct_html}</td></tr>'
        )

    rows_html = "\n".join(rows_html_parts)
    summary = (
        f"{full}/{total} shipped&nbsp;·&nbsp;"
        f"{partial} partial&nbsp;·&nbsp;"
        f"{none_count} not started"
    )

    return f"""
<section>
  <h3>{section}</h3>
  <p class="summary-line">{summary}</p>
  <table class="status-table">
    <thead><tr><th>Feature</th><th>Status</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</section>"""


# ---------------------------------------------------------------------------
# Slide builders
# ---------------------------------------------------------------------------


def _slide_title() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""
<section>
  <h1>gludd</h1>
  <h2 style="color:#ff9800">honestly</h2>
  <p>An autonomous agentic SDLC daemon &mdash; alpha software.</p>
  <p style="font-size:0.6em;color:#888">
    Generated {now} from live README status table.<br>
    Every claim is evidence-backed or labelled aspirational.
  </p>
</section>"""


def _slide_what_is_gludd() -> str:
    return """
<section>
  <h2>What is gludd?</h2>
  <ul>
    <li>Submit a todo &rarr; autonomous agent dispatches it to an AI model</li>
    <li>Validation pipeline: lint &middot; typecheck &middot; test &middot; quality gate</li>
    <li>Work lands in git with a review step and evidence trail</li>
    <li>Execution layer: <strong>Ansible</strong> &mdash; auditable, idempotent, composable</li>
    <li>Event loop: <strong>claim &rarr; dispatch &rarr; review &rarr; reconcile &rarr; repeat</strong></li>
  </ul>
  <p class="honest-note">
    Stability: alpha. Daemon boots, event loop ticks, model gateway calls real APIs.
    Many subsystems are wired but not fully exercised end-to-end.
  </p>
</section>"""


def _slide_completion_summary(data: dict) -> str:
    counts = data["counts"]
    total = counts["total"]
    full = counts["full"]
    partial = counts["partial"]
    none = counts["none"]
    pct_overall = round(full / total * 100) if total else 0

    return f"""
<section>
  <h2>Completion at a Glance</h2>
  <p class="parsed-at">Parsed from README.md &mdash; {data.get("parsed_at", "")[:10]}</p>
  <div class="summary-grid">
    <div class="summary-box shipped">
      <span class="big-num">{full}</span>
      <span class="label">Shipped (100%)</span>
    </div>
    <div class="summary-box partial">
      <span class="big-num">{partial}</span>
      <span class="label">Partial (&gt;0%)</span>
    </div>
    <div class="summary-box none">
      <span class="big-num">{none}</span>
      <span class="label">Not started (0%)</span>
    </div>
    <div class="summary-box total">
      <span class="big-num">{total}</span>
      <span class="label">Total tracked</span>
    </div>
  </div>
  <p style="margin-top:0.8em">
    Overall shipped: <strong>{pct_overall}%</strong> of features at 100%
  </p>
</section>"""


def _slide_competitive(data: dict) -> str:
    """Honest competitive comparison &mdash; clearly labels aspirational items."""
    features = data["features"]

    shipped_examples = [
        f for f in features
        if f["bucket"] == "full" and "Security" not in f.get("section", "")
    ][:5]

    shipped_li = "".join(
        f'<li>{f["title"][:80]}</li>' for f in shipped_examples
    )
    shipped_total = len([f for f in features if f["bucket"] == "full"])
    remainder = max(0, shipped_total - 5)

    return f"""
<section>
  <h2>Shipped vs. Aspirational</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:1em;font-size:0.75em">
    <div>
      <h4 style="color:#4caf50">&#10003; Shipped (representative)</h4>
      <ul style="text-align:left">
        {shipped_li}
        <li>&hellip;and {remainder} more at 100%</li>
      </ul>
    </div>
    <div>
      <h4 style="color:#f44336">&#9888; Aspirational (0% &mdash; design only)</h4>
      <ul style="text-align:left">
        <li>Persistent agent memory</li>
        <li>Offline eval harness</li>
        <li>Semantic codebase retrieval</li>
        <li>Sandboxed code execution</li>
        <li>HITL approval gates</li>
        <li>Multi-agent debate / consensus</li>
        <li>Plan/critique layer</li>
      </ul>
    </div>
  </div>
  <p class="honest-note" style="margin-top:0.6em">
    Aspirational items are design-only. No stub code is presented as working.
  </p>
</section>"""


def _slide_roadmap(data: dict) -> str:
    features = data["features"]
    near_term = [f for f in features if f["bucket"] == "partial"][:6]
    near_li = "".join(
        f'<li>{f["title"][:70]} &mdash; currently {f["pct"]}%</li>'
        for f in near_term
    )

    return f"""
<section>
  <h2>Roadmap</h2>
  <h4>Near-term: finish partial features</h4>
  <ul style="font-size:0.7em;text-align:left">
    {near_li}
  </ul>
  <h4 style="margin-top:0.6em">Longer-term (0% today, aspirational)</h4>
  <ul style="font-size:0.7em;text-align:left">
    <li>Persistent agent memory (G1)</li>
    <li>Offline eval harness (G2) &mdash; blocks self-improvement</li>
    <li>avg_cost column in BenchmarkRepository &mdash; unblocks cost-cap routing</li>
    <li>CI green verified on ubuntu (not just local)</li>
  </ul>
  <p class="honest-note">
    Roadmap order is not guaranteed. Gate is the single source of truth.
  </p>
</section>"""


def _slide_closing() -> str:
    return """
<section>
  <h2>Learn More</h2>
  <ul>
    <li><code>make gate</code> &mdash; run the full quality gate</li>
    <li><code>make test-count</code> &mdash; see how many tests are collected</li>
    <li><code>make dogfood</code> &mdash; run gludd on its own codebase</li>
    <li>README.md &mdash; live feature status table with evidence tokens</li>
    <li>BUGS.md &mdash; documented history of false &ldquo;done&rdquo; claims</li>
  </ul>
  <p class="honest-note">
    Alpha software. Consult the gate, not the slides, for current status.
  </p>
</section>"""


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; }
.reveal h1 { color: #ff9800; font-size: 2.2em; }
.reveal h2 { color: #29b6f6; }
.reveal h3 { color: #ab47bc; font-size: 1.1em; }
.honest-note {
  font-size: 0.55em; color: #aaa; font-style: italic;
  margin-top: 0.8em; border-top: 1px solid #333; padding-top: 0.4em;
}
.summary-line { font-size: 0.65em; color: #ccc; margin: 0.2em 0 0.5em; }
.status-table { width: 100%; font-size: 0.52em; border-collapse: collapse; }
.status-table th {
  background: #1a1a2e; color: #aaa; padding: 3px 6px; text-align: left;
}
.status-table td { padding: 2px 6px; border-bottom: 1px solid #222; vertical-align: middle; }
.row-full td:first-child { color: #c8e6c9; }
.row-partial td:first-child { color: #ffe0b2; }
.row-none td:first-child { color: #ef9a9a; }
.pct-bar {
  display: inline-block; width: 60px; height: 8px;
  background: #333; border-radius: 4px; vertical-align: middle;
  position: relative; overflow: hidden;
}
.pct-fill { display: block; height: 100%; border-radius: 4px; }
.pct-num { font-size: 0.9em; color: #ccc; }
.summary-grid {
  display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 0.8em;
  margin: 0.8em 0;
}
.summary-box {
  border-radius: 8px; padding: 0.8em 0.4em;
  display: flex; flex-direction: column; align-items: center; gap: 0.3em;
}
.summary-box .big-num { font-size: 2em; font-weight: bold; }
.summary-box .label { font-size: 0.6em; color: #ccc; }
.summary-box.shipped { background: #1b5e20; }
.summary-box.partial { background: #e65100; }
.summary-box.none { background: #b71c1c; }
.summary-box.total { background: #1a237e; }
.parsed-at { font-size: 0.55em; color: #666; margin: 0 0 0.5em; }
"""

_REVEAL_CDN = "https://cdn.jsdelivr.net/npm/reveal.js@5.1.0"

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>gludd &mdash; honestly</title>
<link rel="stylesheet" href="{cdn}/dist/reset.css">
<link rel="stylesheet" href="{cdn}/dist/reveal.css">
<link rel="stylesheet" href="{cdn}/dist/theme/night.css">
<style>{css}</style>
</head>
<body>
<div class="reveal">
  <div class="slides">
{slides}
  </div>
</div>
<script src="{cdn}/dist/reveal.js"></script>
<script>
  Reveal.initialize({{
    hash: true,
    slideNumber: true,
    controls: true,
    progress: true,
    history: true,
    center: true,
    transition: 'slide',
  }});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build(out_path: Path, data_path: str | None) -> None:
    data = _load_features(data_path)
    features = data["features"]

    # Group by section (preserve first-seen order)
    sections: dict[str, list[dict]] = {}
    for f in features:
        sec = f.get("section") or "Misc"
        sections.setdefault(sec, []).append(f)

    slides_parts: list[str] = [
        _slide_title(),
        _slide_what_is_gludd(),
        _slide_completion_summary(data),
    ]

    # One slide per section (status tables)
    for sec, rows in sections.items():
        slides_parts.append(_section_slide(sec, rows))

    slides_parts += [
        _slide_competitive(data),
        _slide_roadmap(data),
        _slide_closing(),
    ]

    slides_html = "\n".join(slides_parts)
    html = _HTML_TEMPLATE.format(
        cdn=_REVEAL_CDN,
        css=_CSS,
        slides=slides_html,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(
        f"build_deck: wrote {out_path} "
        f"({len(features)} features, {len(sections)} sections, "
        f"{len(slides_parts)} slides)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build reveal.js deck from README status")
    parser.add_argument(
        "--out",
        default="docs/presentation/build/index.html",
        help="Output HTML path",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Pre-parsed features JSON (default: auto-parse README.md)",
    )
    args = parser.parse_args()
    build(Path(args.out), args.data)


if __name__ == "__main__":
    main()
