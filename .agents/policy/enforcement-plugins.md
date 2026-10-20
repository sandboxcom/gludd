## CRITICAL: Enforcement Plugin Reference

Read this before plugin or enforcement work. The root
[AGENTS.md](../../AGENTS.md) owns policy and precedence; the
[key Make guide](key-make-targets.md) owns the ordered operating path.
Run `make list-plugins` for the current registered roster, hook families and
configuration knobs. Registration order in opencode.json determines hook order.

### Current dispatch policy

The controlling cost-efficiency rule is default adaptive minimum0, hard maximum3,
with an operator floor0..3. Focused inline work is valid. Historical three-agent
floors and streak descriptions cannot raise that minimum or justify filler.
A configured positive floor applies only within the root policy's useful-work,
release-priority, resource and ownership limits.

Before mutations, read tracking state and establish ownership through the native
owners. A roster entry is not proof that its host loaded the current code.
Report model-agent names/status separately from actual native subprocess PIDs.

### Diagnose a block through its owner

Read the actual denial and identify its failing input. Use permitted read paths
to inspect that input; read-exempt hooks do not confer access outside the five
authorized prefixes. Retain failed evidence and repair the implementation or
configuration that caused the denial.

Do not disable or weaken a guard to get past noise, an error or friction.
Do not add another observer, runner, daemon or cache. Use the existing
Make owner and the root guardrail-integrity recovery rules. A documented
configuration knob is not blanket authorization to turn off enforcement.
The `hasRealPendingWork` protection remains active through all recovery paths;
empty todos or a heuristic reset cannot erase pending task/gate/CI evidence.

The [versioned policy source](../../docs/AGENT_POLICY_V1_SOURCE_3583000.md)
retains historical plugin tables and incident context. Those inventories and
old disable examples are historical evidence, not the current operating recipe.

### Verify code and runtime activation separately

After a plugin change, run the relevant failing-first behavioral owner and
`make test-hook-runtime`. Run `make check-node-v26-compat` for native stripping,
`make verify-plugin-manifest` for registration/source correspondence, and
`make verify-enforcement` for the existing enforcement checks. Preserve each
owner's native exit status and failure logs; structural checks alone are
insufficient behavioral evidence.

Any .opencode/plugin/*.ts code edit requires an application restart. Inform the
user and continue under the existing loaded behavior until that restart.
Compiled /tmp modules are loaded at startup; hot-module fallback or state reload
does not remove this duty. State-only tuning may use its documented native
reload owner; it does not activate changed plugin source.

After restart, attempt a text-only response once to verify actual enforcement.
If it succeeds while real work is pending, investigate rather than claiming the
hook works. Runtime activation, source acceptance, full gate and exact-SHA CI
remain distinct evidence. Follow the root release and completion gates.
