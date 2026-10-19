# Agent policy v1 native context measurements

Measurements below came from the existing make audit-agent-context-size owner on the isolated agent-agents-compression-20261019 branch, using canonical Python3.11/no-sync/downloads-never. No tokenizer estimate or whole-project green claim is made. The previous v2 draft's155-line/6.2% figure does not apply.

## Captured sizes

| Context set | Original physical lines | Compact physical lines | Original UTF-8 bytes | Compact UTF-8 bytes |
|---|---:|---:|---:|---:|
| AGENTS root |2499|536|231107|69557|
| AB060 default: root + docs/CLAUDE.md |2650|687|241636|80086|
| Default plus both existing conditional policy references |2840|877|252497|90947|

The last row adds .agents/policy/key-make-targets.md and .agents/policy/enforcement-plugins.md to BOTH sides. Those preexisting references remain unchanged and keep their original triggers. docs/CLAUDE.md is AB060's existing second file, not root CLAUDE.md.

The optional release-protocol appendix is a separate topic exposure: compact default+both references+AGENT_POLICY_V1_RELEASE_HANDOFF.md measured955 physical lines/99493bytes after the last equivalent literal restoration. It is needed for that future reconciliation procedure, not an always-read include for unrelated work. Full archival comparison can exceed the original context size; root reduction is not a claim that loading every historical/context file saves tokens.

Exact root identities:
- Original archived source SHA256:9abf5a809d036cbd205e67549c0291186d4c0bb4d7b896789814492027ac386c.
- Compact root SHA256:f2d07f6eb4411759c93c15984716ccac758bb73b2c9336998cf68235d06509d1.
- Source Git base:3583000e3b315f67acf0292dec8ba38642317a1c.

## Native owner and reproduction

AB060 now accepts an explicit read-only file set through AGENT_CONTEXT_FILES, a JSON array of repository-relative paths, and literal AGENT_CONTEXT_JSON=0|1. Make transports raw values as data, never as recursive Make/shell source. Empty file carrier preserves the existing two-file default; explicit empty arrays, malformed/missing/duplicate/traversing/absolute/leaf-symlink/nonregular inputs fail. Normalized relative aliases cannot double-count a file. The audit measures captured bytes once and reports physical lines, bytes and SHA256 per file.

Legacy total_lines retains the former Path.read_text universal-newline translation (CR,CRLF,LF) from the SAME captured bytes, then newline-count+1 per file. physical_lines counts raw LF separators plus a final unterminated fragment; hashes/bytes stay raw. These are intentionally distinct. Its20,000-line failure threshold is unchanged. threshold_kb600 remains diagnostic-only as in the original owner. An AB060 PASS proves neither semantic preservation nor compactness. This change adds no second scanner or tokenizer.

```text
make audit-agent-context-size AGENT_CONTEXT_FILES='["docs/AGENT_POLICY_V1_SOURCE_3583000.md"]' AGENT_CONTEXT_JSON=1
make audit-agent-context-size AGENT_CONTEXT_FILES='["AGENTS.md"]' AGENT_CONTEXT_JSON=1
make audit-agent-context-size AGENT_CONTEXT_FILES='["AGENTS.md","docs/CLAUDE.md",".agents/policy/key-make-targets.md",".agents/policy/enforcement-plugins.md"]' AGENT_CONTEXT_JSON=1
make audit-agent-context-size AGENT_CONTEXT_FILES='["docs/AGENT_POLICY_V1_SOURCE_3583000.md","docs/CLAUDE.md",".agents/policy/key-make-targets.md",".agents/policy/enforcement-plugins.md"]' AGENT_CONTEXT_JSON=1
```

All Python-running invocations use:
UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11,
UV_NO_SYNC=1, UV_PYTHON=3.11, UV_PYTHON_DOWNLOADS=never.
The Make contract's documented two-file JSON behavioral example passed. No install occurred.

## Acceptance receipts and limits

- Initial meaningful metrics RED: the old no-argument AB060 API refused the explicit file set and lacked physical_lines (2FAIL). The first metrics/compatibility green passed33 cases; actual Make-expression values remain inert. Independent review identified normalized aliases, CR-only parity and FIFO-mode pre-read refusal; the author's native RED then produced5 failures/35passes, preserved at /tmp/gludd-agents-metrics-boundary-red.xml.
- Final native coverage-files after those bounded repairs:40PASS/2.28s; the entire existing scripts/audit_agent_behavior.py reports97% combined line/branch coverage. Native85% aggregate/75% per-file audit passes with0 files below threshold. Raw report .gate-logs/coverage-agent-context-metrics-boundaries.json; observer coverage-files/20261019T093215Z-60413-9c92f4de passedrc0/3.9s with native log/trace/status retained. The earlier33PASS/2.25s,97% report .gate-logs/coverage-agent-context-metrics.json and observer20261019T091515Z-32069-7905f9ea remain valid historical green before the new controls.
- The27-whole-owner cohort before the three metrics-boundary repairs passes565/0skip in15.06s under fatal warnings; actual JUnit /tmp/gludd-agents-compression-27-owner-final.xml. The complete path order is [CONFORMANCE](AGENT_POLICY_V1_CONFORMANCE.txt). Existing instruction assertions were restored in the compact policy, never weakened.
- Earlier native receipts remain failed:521PASS/11FAIL at /tmp/gludd-agents-compression-conformance-521pass11fail.xml;531PASS/1FAIL at /tmp/gludd-agents-compression-conformance-531pass1fail.xml;32PASS/1FAIL at /tmp/gludd-agents-compression-doc-contract-32pass1fail.xml. The pre-conformance505-line draft is /tmp/gludd-agents-compression-applied-505-pre-conformance.md.
- An initial pytest-cov invocation selected a file path as a module and produced no data; it failed the85% floor and emitted a report warning. It is not coverage proof. The corrected run used the existing native coverage-files owner with config/coverage_agent_context_metrics.ini and retained the unchanged floors.
- Strict mypy passes all3 code/test files; scoped Ruff passes; native Make contract246PASS, resource inventory261PASS and file-line policyPASS. Main-owned locked Rumdl checked the frozen root/topic/map/handoff docs; no warning suppression was added.

Independent clause preservation review is separate from these tests: /tmp/gludd-agents-compression-applied-independent-review.md records the reviewer reading the full original/root/references and correcting13 findings. It excludes metrics/code/Make/config and live adoption. The final equivalent target-variable literal, enlarged27-owner handoff/cohort and bounded metrics repairs have peer policy/code review; final immutable committed-SHA binding still requires review.

TASKS/SESSION ownership release, normal guarded commit/fresh collection and authorized integration remain separate work. This file does not certify a full gate, exact-SHA hosted proof, task completion, release readiness, promotion or publication.
