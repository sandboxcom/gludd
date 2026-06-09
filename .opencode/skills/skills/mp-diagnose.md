---
{
  "name": "mp-diagnose",
  "description": "Disciplined diagnosis loop for hard bugs: reproduce, minimise, hypothesise, instrument, fix, regression-test. Adapted from mattpocock/skills.",
  "tags": [
    "mattpocock",
    "debugging",
    "diagnosis",
    "engineering"
  ],
  "category": "engineering"
}
---

# Diagnose

A 6-phase discipline for hard bugs:

1. **Build a feedback loop** — Spend disproportionate effort here. Use failing tests, curl scripts, CLI invocations, headless browsers, throwaway harnesses. Iterate to make the loop faster and more deterministic.
2. **Reproduce** — Run the loop. Confirm the failure matches the user's description.
3. **Hypothesise** — Generate 3-5 ranked, falsifiable hypotheses. Show to user before testing.
4. **Instrument** — One probe per hypothesis, one variable at a time. Prefer debugger/REPL over targeted logs. Tag debug logs with unique prefix.
5. **Fix + regression test** — Write regression test before fix. If no test seam exists, that itself is the finding.
6. **Cleanup + post-mortem** — Verify repro gone, regression test passes, all instrumentation removed. Ask 'what would have prevented this bug?'

