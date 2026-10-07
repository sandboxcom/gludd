<!-- behavioral-spec-prologue:start -->
# BEHAVIORAL ENFORCEMENT SPECIFICATIONS — 4000 numbered specs

**Version:** 4.0
**Date:** 2026-07-20
**Status:** Active — corresponding enforcement mechanisms tracked in `tests/unit/test_behavioral_specs.py`

---
<!-- behavioral-spec-prologue:end -->

## Routed specification corpus

The numbered specifications now live in the ordered shards below. Consumers
must use `scripts.behavioral_specs.load_behavioral_specs`; it composes the exact
historical source, verifies the SHA-256 marker, and fails closed for a missing,
duplicate, corrupt, or unrouted shard. Every `### ID — title` heading is retained
verbatim, so IDs and generated heading anchors remain stable inside their routed
file.

The split is a zero-downtime documentation migration: writers stage and verify
all shards before atomically replacing this small routing index. Rollback means
reverting the index, loader, and shard set together; the pinned semantic digest
reconstructs and verifies the pre-split monolith before that rollback is used.

Practitioner reports explain both sides of the boundary. VS Code issue
[#301936](https://github.com/microsoft/vscode/issues/301936) records severe edit
latency once Markdown grows beyond 10,000 lines. GitHub Community discussion
[#4511](https://github.com/orgs/community/discussions/4511) records the long-lived
lack of file-level redirects after content moves, while discussion
[#60861](https://github.com/orgs/community/discussions/60861) records anchor-case
failures. The bounded index, relative routes, lowercase generated anchors, and
byte-for-byte corpus digest address those operational failure modes.

<!-- behavioral-spec-source-sha256: 36f7cbb2a90b3ee606a732433ce18b3133d46fc159cb5ffbb70d71fb5dcd851c -->

### Routes

| Stable ID range | Source sections |
| --- | --- |
| [AA001–R20](behavioral/01-aa001-r20.md#aa001--push-cancels-ci-enforcement) | AA001-AA100 — Session 2026-07-18/20 Incident Specs; AC001-AC020 — Release Pipeline Integrity Specs; Group P — Push Discipline (P01–P30); Group B — Branch Discipline (B01–B25); Group O — Objective Tracking (O01–O30); Group T — Test Integrity (T01–T30); Group D — Dispatch Concurrency (D01–D30); Group S — Anti-Stop (S01–S25); Group E — Anti-Essay (E01–E20); Group M — Merge Safety (M01–M20); Group G — Gate Discipline (G01–G20); Group R — Release Discipline (R01–R20) |
| [W01–H100](behavioral/02-w01-h100.md#w01--every-file-editing-subagent-works-in-an-isolated-worktree) | Group W — Worktree Discipline (W01–W30); Group F — CI Discipline (F01–F30); Group C — Commit Discipline (C01–C30); Group Q — Quality Gate (Q01–Q30); Group X — Subagent Discipline (X01–X30); Group A — Audit Discipline (A01–A30); Group N — Naming/Code Standards (N01–N30); Group K — Knowledge/Context Management (K01–K30); Group U — User Intent Enforcement (U01–U30); Group Z — Zero-Failure Enforcement (Z01–Z30); Group H — Hard-Break Discipline (H01–H100) |
| [V01–Y100](behavioral/03-v01-y100.md#v01--every-status-claim-requires-machine-produced-evidence) | Group V — Verification Discipline (V01–V100); Theme: User intent overrides all other priorities. Primary objective is THE goal. Side-tasks are forbidden when objective unmet. Decision-making discipline.; Theme; Anti-Stop Enforcement While CI is Pending (Y01–Y15); Release-Completeness as Stop-Gate (Y16–Y30); Objective-Driven Continuation (Y31–Y45); Exhaustion-Based Stopping Criteria (Y46–Y60); Background-Operation Non-Blocking (Y61–Y75); Session-Continuity Discipline (Y76–Y90); Auto-Resume Patterns & Idle/Dormant Prohibition (Y91–Y100); Coverage Matrix; Audit Log; Expansion: Push Discipline (P31–P120) |
| [P31–E45](behavioral/04-p31-e45.md#p31--push-must-not-overlap-with-gate-execution) | Expansion: Push Discipline (P31–P120); Expansion: Branch Discipline (B26–B120); Expansion: Objective Tracking (O31–O120) |
| [E46–W120](behavioral/05-e46-w120.md#e46--essay-prevention-response-that-is-90-tool-output-echo--commentary-blanked) | Expansion: Gate Discipline (G21–G125); Expansion: Release Integrity (R21–R125); Expansion: Worktree Isolation (W31–W120) |
| [F31–A125](behavioral/06-f31-a125.md#f31--filesafety-enforcement-guard-31-automated-unique-mechanism) | Expansion: File Safety (F31–F120); Expansion: Context Freshness (C31–C120); Expansion: Quality Gate (Q31–Q125); Expansion: Subagent Discipline (X31–X120); Expansion: Audit Completeness (A31–A125) |
| [N31–I102](behavioral/07-n31-i102.md#n31--namingcodequality-enforcement-guard-31-automated-unique-mechanism) | Expansion: Naming/Code Quality (N31–N120); Expansion: Knowledge Management (K31–K120); Expansion: User Intent (U31–U120); Expansion: Zero-Failure (Z31–Z125); Expansion: Intent Priority (I01–I120) |
| [P121–W154](behavioral/08-p121-w154.md#p121--the-agent-must-warn-when-pushing-while-sibling-branches-have-unmerged-upstream-changes) | Expansion: Push Discipline (P121–P154) (34 specs); Expansion: Branch Discipline (B121–B154) (34 specs); Expansion: Objective Tracking (O102–O154) (53 specs); Expansion: Test Integrity (T101–T154) (54 specs); Expansion: Dispatch Floor (D100–D154) (55 specs); Expansion: Stop Prevention (S100–S154) (55 specs); Expansion: Essay Prevention (E100–E154) (55 specs); Expansion: Merge Safety (M126–M154) (29 specs); Expansion: Gate Discipline (G126–G154) (29 specs); Expansion: Release Integrity (R126–R154) (29 specs); Expansion: Worktree Isolation (W121–W154) (34 specs) |
| [F121–J153](behavioral/09-f121-j153.md#f121--the-agent-must-not-access-files-outside-the-workspace-or-tmpgludd--paths) | Expansion: File Safety (F121–F154) (34 specs); Expansion: Context Freshness (C121–C154) (34 specs); Expansion: Quality Gate (Q126–Q154) (29 specs); Expansion: Subagent Discipline (X121–X154) (34 specs); Expansion: Audit Completeness (A126–A154) (29 specs); Expansion: Naming/Code Quality (N121–N154) (34 specs); Expansion: Knowledge Management (K121–K154) (34 specs); Expansion: User Intent (U121–U154) (34 specs); Expansion: Zero-Failure (Z126–Z154) (29 specs); Expansion: Hard Break Enforcement (H101–H154) (54 specs); Expansion: Verification Enforcement (V101–V154) (54 specs); Expansion: Judgment Enforcement (J101–J153) (53 specs) |
| [L101–I153](behavioral/10-l101-i153.md#l101--the-agent-must-learn-which-model-profile-routes-to-which-actual-model-for-dispatch-accuracy) | Expansion: Learning Enforcement (L101–L153) (53 specs); Expansion: Yield Enforcement (Y101–Y153) (53 specs); Expansion: Intent Priority (I103–I153) (51 specs) |

The following manifest is the loader's single ordered source of truth. A route
must appear exactly once and must resolve beneath `docs/specs/behavioral/`.

<!-- behavioral-spec-shards:start -->
- `behavioral/01-aa001-r20.md`
- `behavioral/02-w01-h100.md`
- `behavioral/03-v01-y100.md`
- `behavioral/04-p31-e45.md`
- `behavioral/05-e46-w120.md`
- `behavioral/06-f31-a125.md`
- `behavioral/07-n31-i102.md`
- `behavioral/08-p121-w154.md`
- `behavioral/09-f121-j153.md`
- `behavioral/10-l101-i153.md`
<!-- behavioral-spec-shards:end -->
