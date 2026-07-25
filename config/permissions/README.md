# Permission Specs

Every entity that acts inside gludd — a human user, an agent type, or an STS
token delegated to a subagent — carries a **`PermissionSpec`** that defines
what file paths it can read/write, what hosts it can connect to, what OpenBao
secret paths it can access, and what capabilities are explicitly denied to it.

The YAML files in this directory are the at-rest defaults. The canonical
runtime types live in
[`src/general_ludd/security/permissions.py`](../../src/general_ludd/security/permissions.py)
(`PermissionSpec`, `Capability`, `PermissionSpecParser`). Formal intersection
semantics are documented in
[`docs/design/PERMISSION_SYSTEM.md`](../../docs/design/PERMISSION_SYSTEM.md) §10.

---

## Anatomy of a `PermissionSpec`

```yaml
version: 1
agent_type: primary                 # label identifying the spec source
subject: human                       # agent | human | sts_token (optional; default agent)
parent_agent_id: null                # set on STS tokens minted by delegation
max_sts_ttl_seconds: 3600            # hard ceiling for any token minted FROM this spec
max_subagent_permissions: same_or_fewer   # delegation policy (only value supported)

capabilities:                        # positive grants
  - resource: file:repo              # <domain>:<backend> dotted name
    actions: ["read", "write"]
    constraints:
      path_prefix: "/repo/"          # backend-specific scope

  - resource: net:egress:llm_api
    actions: ["connect"]
    constraints:
      allowed_hosts: ["api.anthropic.com", "api.openai.com", "api.z.ai"]

  - resource: secret:openbao
    actions: ["read", "write"]
    constraints:
      openbao_paths: ["secret/data/gludd/*"]

denied:                              # negative grants (always win over positives)
  - resource: agent:ornith
    actions: ["solve", "improve"]
    constraints: {}
```

### Fields

| Field | Type | Meaning |
|---|---|---|
| `version` | int | Schema version. Always `1`. |
| `agent_type` | str | Label identifying the spec source. Becomes `"sts_token"` on intersection results. |
| `subject` | str | `agent` (default), `human`, or `sts_token`. Selects which enforcement path applies. |
| `parent_agent_id` | str\|null | The agent_id of the issuer when this spec arrived via STS delegation. |
| `parent_human_id` | str\|null | The human_id whose spec participated in the intersection (audit only). |
| `max_sts_ttl_seconds` | int | Hard ceiling for any STS token minted FROM this spec. Intersection takes the MIN. |
| `max_subagent_permissions` | str | Delegation policy. Only `"same_or_fewer"` is supported. |
| `capabilities` | list[Capability] | Positive grants. Each is a `(resource, actions, constraints)` triple. |
| `denied` | list[Capability] | Negative grants. **Always win** over `capabilities`, regardless of order. |

### Resources

Resources are dotted names `<domain>:<backend>`. The resource family
determines which constraint keys are honored:

| Family | Example resource | Constraint keys |
|---|---|---|
| `file:` | `file:repo`, `file:tmp`, `file:` | `path_prefix` (glob via `fnmatch`) |
| `net:` | `net:egress:any`, `net:egress:llm_api` | `allowed_hosts`, `allowed_ports` |
| `secret:openbao` | `secret:openbao` | `openbao_paths` (list of glob patterns) |
| `agent:` | `agent:ornith` | backend-specific (e.g. `max_iterations`, `max_tokens_per_call`) |
| `git:` | `git:commit` | none |

Actions: `read`, `write`, `list`, `delete` for secrets; `connect` for
network; `read`/`write` for files; family-specific verbs for other domains.

### The deny predicate

A `denied` capability matches (and therefore BLOCKS the request) when **all**
of:

1. its `resource` equals the requested resource; AND
2. its `actions` list is empty (deny-ALL) OR contains the requested action; AND
3. it carries no path constraint, OR one of its `openbao_paths`/`path_prefix`
   patterns matches the requested path.

Denials propagate through the whole delegation chain via `StsIssuer.validate`,
`is_subset`, and `SecretsManager._enforce_permission` — a denial can never be
inert.

---

## Intersection (subagent delegation)

When an agent dispatches a subagent, the effective scope is the
**intersection** (lowest-common-subset) of three specs:

```
effective_spec = intersection(human_spec, agent_spec, requested_spec)
```

Implemented by `PermissionSpecParser.intersection` in
[`permissions.py`](../../src/general_ludd/security/permissions.py). Wired in
`src/general_ludd/event_loop/loop.py::_resolve_permission_spec`.

### Intersection rules

For each capability that appears on **both** sides with the same `resource`:

| Dimension | Rule |
|---|---|
| `actions` | set intersection (`&`) |
| `path_prefix` (`file:`) | the NARROWER prefix wins when one truly contains the other (segment-wise comparison, not bare `startswith`); **disjoint scopes drop the capability** — never widen |
| `allowed_hosts` / `allowed_ports` (`net:`) | set intersection; empty intersection drops the capability |
| `openbao_paths` (`secret:openbao`) | set intersection; empty drops the capability |

Whole-spec fields:

| Field | Rule |
|---|---|
| `denied` | **UNION** of both denied lists |
| `max_sts_ttl_seconds` | MIN of the two |
| `subject` | `PermissionSubject.STS_TOKEN` (a derived scope) |
| `parent_agent_id` / `parent_human_id` | recorded for audit |

If two capabilities share a resource but their intersected actions or
constraints are empty, the capability is **dropped entirely**. Conservative
default: when unsure whether to grant, deny.

### Worked example

A `human-operator` (file write under `/repo/`) dispatches a `subagent`
(file write under `/tmp/gludd/` only):

- intersection of `file:` capabilities → **no shared file scope** (disjoint
  path prefixes) → subagent cannot write `/repo/` or `/tmp/` from the human's
  authority; it retains only `/tmp/gludd/` from its own spec
- intersection of `net:egress:` → only hosts both lists permit
- intersection of `secret:openbao` → only paths both lists permit (likely
  empty for a default subagent, which has no secret capability)
- `denied` lists unioned → anything either side forbids stays forbidden

---

## Escalation requests

When an agent needs a capability outside its current spec, it requests an
escalation via the daemon API rather than failing silently.

### Endpoint

```
POST /admin/perm/escalation-request
```

Body:

```json
{
  "agent_id": "primary-1",
  "current_spec_yaml": "<yaml of agent's current PermissionSpec>",
  "requested_additional_capabilities": [
    {
      "resource": "secret:openbao",
      "actions": ["write"],
      "constraints": {"openbao_paths": ["secret/data/gludd/overrides/*"]}
    }
  ],
  "reason": "Need to persist an override key for run #42",
  "alternatives_tried": [
    {"approach": "re-use existing key", "outcome": "key rotation policy forbids"},
    {"approach": "ask human to write", "outcome": "human offline, run is time-critical"},
    {"approach": "use build/* subtree", "outcome": "wrong mount, build keys are not override keys"}
  ]
}
```

### Validation

- **All four fields are required** (`agent_id`, `current_spec_yaml`,
  `requested_additional_capabilities`, `reason`). Missing any → HTTP 400.
- **≥3 distinct `alternatives_tried`** must be documented. Fewer → HTTP 422
  with `insufficient alternatives_tried`.

### Auto-approval vs pending

| Outcome | Condition | Action |
|---|---|---|
| `auto_approved` | Requested capabilities are a strict subset of `human_spec ∩ agent_spec` | STS token minted scoped to `(current + requested) ∩ human_spec`, returned in response |
| `pending` | Requested capabilities lie outside the intersection | A `HumanTodo` (category `permission_escalation`) is filed; human resolves via CLI |

Humans resolve pending requests via:

```
gludd perm escalations                      # list pending/all
gludd perm escalations <id> approve         # grant — STS scoped to (current + requested) ∩ human_spec
gludd perm escalations <id> deny            # reject
gludd perm escalations history              # audit trail
```

Approval mints an STS token scoped to `(current + requested) ∩ human_spec` —
**humans cannot grant more than they themselves have**.

---

## Default human roles

| Role | Files | Network | Secrets | Notes |
|---|---|---|---|---|
| `human-admin` (`human-admin.yml`) | read+write `/` (entire filesystem) | `*` (any host) | read+write+list+delete `secret/data/gludd/*` | Full access. Use only for operators who own the deployment. |
| `human-operator` (`human-operator.yml`) | read+write `/repo/` | `*` (any host) | read `secret/data/gludd/*` | Default role selected by daemon config `default_human_role`. Can run playbooks and read secrets but cannot mutate OpenBao config or delete secrets. |
| `human-viewer` (`human-viewer.yml`) | read `/repo/` only | `api.anthropic.com`, `api.openai.com`, `api.z.ai` | read `secret/data/gludd/read-only/*` | Least privilege. Read-only inspection of repo and LLM APIs. |

Switch the active default via daemon config:

```yaml
default_human_role: human-operator     # or human-admin / human-viewer
```

Per-user overrides: place a YAML file in `config/permissions/<user>.yml`
with `subject: human` and the daemon will pick it up by `agent_type`.

---

## Creating a custom permission spec

1. **Pick an `agent_type` name** (e.g. `agent-research`, `task:review_pr`).
2. **Create the YAML file** in this directory following the anatomy above.
   Start from the most restrictive existing spec (e.g. `subagent.yml`) and
   widen one capability at a time.
3. **Set `subject`** appropriately:
   - `agent` for a new agent type
   - `human` for a new human role
   - leave unset for the default (`agent`)
4. **Set `max_sts_ttl_seconds`** conservatively. Pick the shortest TTL that
   still lets the agent finish its task. Intersection always takes the MIN,
   so a low TTL here caps every downstream token.
5. **Validate** before committing:

   ```bash
   make test TESTFILE=tests/unit/test_permission_intersection.py
   make test TESTFILE=tests/unit/test_permissions.py
   ```

6. **Wire it in**. For agent specs, the daemon loads them by `agent_type`
   from this directory at startup. For human roles, also update
   `default_human_role` (or add a per-user override) so the daemon picks
   the right spec.

### Tips

- **Denials are absolute.** A `denied` entry on `human-operator` cannot be
  re-granted by intersection — it propagates to every subagent the operator
  dispatches.
- **Disjoint path prefixes drop the capability.** If your custom spec uses
  `path_prefix: /data/` and the issuer uses `/repo/`, the intersection is
  empty — no file access is granted from either side. Align prefixes with
  the issuer or use `path_prefix: /` (admin only).
- **Intersection is conservative.** When in doubt, it drops the capability.
  Design custom specs so their constraints *overlap* with the issuer's
  (human ∩ agent) — a custom spec narrower than the issuer's intersection
  is the safe path.
- **Test the intersection.** The unit test `tests/unit/test_permission_intersection.py`
  covers the formal semantics; add a case for any non-trivial custom spec.

---

## File reference

| File | `agent_type` | Subject | Scope |
|---|---|---|---|
| `human-admin.yml` | `human-admin` | human | Full filesystem + any host + read/write/list/delete on `secret/data/gludd/*` |
| `human-operator.yml` | `human-operator` | human | `/repo/` read+write, any host, secret read under `secret/data/gludd/*` |
| `human-viewer.yml` | `human-viewer` | human | `/repo/` read-only, three LLM API hosts, read-only secrets under `secret/data/gludd/read-only/*` |
| `primary.yml` | `primary` | agent | The orchestrator. `/repo/` + `/tmp/gludd/` read+write, three LLM API hosts, `secret/data/gludd/*` read+write. |
| `subagent.yml` | `subagent` | agent | `/tmp/gludd/` read+write, Anthropic API only. Secrets and `agent:ornith` explicitly denied. |
| `build.yml` | `build` | agent | `/repo/` + `/tmp/gludd/` read+write, three LLM hosts, `secret/data/gludd/build/*` read+write, can invoke `agent:ornith` (≤50 iterations, ≤100k tokens/call). |
| `agent-ornith.yml` | `agent-ornith` | agent | `/worktree/ornith/` read+write, localhost-only on ports 8000/3000. 5-minute TTL. |
| `task_implement_change.yml` | `task:implement_change` | agent | `/tmp/gludd/` read+write only. 15-minute TTL. Most restrictive non-subagent spec. |

### TTL quick reference

| Spec | `max_sts_ttl_seconds` |
|---|---|
| `human-admin.yml` | 3600 (1h) |
| `human-operator.yml` | 3600 (1h) |
| `human-viewer.yml` | 3600 (1h) |
| `primary.yml` | 3600 (1h) |
| `build.yml` | 3600 (1h) |
| `task_implement_change.yml` | 900 (15m) |
| `subagent.yml` | 1800 (30m) |
| `agent-ornith.yml` | 300 (5m) |
