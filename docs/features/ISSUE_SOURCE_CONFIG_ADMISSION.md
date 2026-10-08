# Issue-source configuration admission

## Outcome

Issue #75 now has an additive, fail-closed configuration boundary for its
first two built-in providers: `github` and `jira`. The boundary validates
configuration and returns immutable shadow plans. It does **not** import an
adapter, resolve a credential, access the network or filesystem, start a
process or thread, schedule polling, ingest an issue, or perform write-back.

This is intentionally narrower than the existing adapter inventory. GitLab,
Redmine, ServiceNow, local Markdown/CSV sources, third-party packages, provider
factories, and runtime/event-loop wiring remain deferred. An unrecognized
`source` tag is a startup validation error; it never falls through to dynamic
import or a generic dictionary.

## Configuration contract

The existing `UserConfig.issues` block is unchanged. The new field is optional
and defaults to an empty list, so an upgrade with no configuration has exactly
the legacy behavior:

```yaml
issue_sources:
  - source: github
    name: github-main
    mode: shadow
    repository: sandboxcom/gludd
    token_env: GITHUB_TOKEN
    poll_interval_seconds: 300
    page_limit: 5
    record_limit: 100

  - source: jira
    name: jira-acme
    mode: shadow
    base_url: https://acme.atlassian.net
    project_key: GLUDD
    email_env: JIRA_EMAIL
    token_env: JIRA_API_TOKEN
    poll_interval_seconds: 300
    page_limit: 5
    record_limit: 100
```

`source` is the discriminator for a Pydantic tagged union. Each provider has
an explicit schema and `extra="forbid"`; `source: plugin`, a misspelled key, or
a literal `token`/`password` therefore fails closed. Credential fields accept
only uppercase environment-variable identifiers. They never accept or resolve
the secret value. The Jira origin must use HTTPS and cannot embed user info, a
query, or a fragment.

| Invariant | Admitted range |
|---|---:|
| Source declarations | 0–16 |
| Unique source name | lowercase ASCII slug, 1–64 characters |
| Mode | `shadow` only |
| Poll interval | 60–86,400 seconds |
| Pages per future poll | 1–10 |
| Records per future poll | 1–500 |

The whole list can be supplied as JSON through `GLUDD_ISSUE_SOURCES`. Per-index
nested environment variables are deliberately not part of this contract.

## Admission output and safety boundary

`admit_issue_sources()` accepts already validated built-in declarations and
returns `tuple[IssueSourceAdmissionPlan, ...]`. Both the outer tuple and each
frozen/slotted plan are immutable. Provider parameters and credential variable
names are tuples as well, so a caller cannot mutate a plan after admission.
Only sanitized locators and environment-variable **names** survive admission.

The admission module imports Pydantic and standard-library types only. It does
not import `github_issues.py` or `jira.py`. Tests replace file, socket,
subprocess, and thread entry points with traps while validating and admitting
both providers, and assert that neither provider module was loaded. This pins
the boundary as configuration-only rather than a hidden startup action.

## Why these constraints

- Pydantic recommends discriminated unions because they select one declared
  member predictably and avoid ambiguous untagged-union matching. That gives
  the built-in provider allowlist an additive extension point without accepting
  unknown types: [Pydantic discriminated unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions).
- PyPA documents naming conventions, namespace packages, and package metadata
  as plugin-discovery mechanisms. Every option performs discovery and may lead
  to imports, so third-party discovery is explicitly outside this inert
  admission phase: [PyPA plugin discovery guide](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/).
- A reported `pydantic-settings` regression showed discriminated unions failing
  when assembled from nested delimiter keys. The stable contract here accepts
  the complete list as one JSON environment value and has a regression test for
  that path: [pydantic-settings issue #422](https://github.com/pydantic/pydantic-settings/issues/422).
- A PyGithub user reported a search list whose advertised count stopped at
  1,000 and whose iteration returned only 1,020 of 3,041 results. A future poller
  therefore must not interpret a provider count as completeness; bounded pages
  and records are explicit admission inputs:
  [PyGithub issue #1309](https://github.com/PyGithub/PyGithub/issues/1309).
- Atlassian says paginated endpoint limits differ and can change without
  notice, and that a later requested page can be empty. This supports explicit
  local work caps rather than assuming a server maximum:
  [Jira REST v3 pagination](https://developer.atlassian.com/cloud/jira/platform/rest/v3/#pagination).
- Practitioner reports reinforce the same operational limit. Atlassian
  Community answers recommend advancing `startAt` with `maxResults` and state
  that an endpoint limit cannot be bypassed, so future fetching must page within
  the admitted caps instead of requesting an unbounded result:
  [pagination example](https://community.atlassian.com/forums/discussion/516052/how-to-do-pagination-on-jira-rest-api),
  [endpoint-limit discussion](https://community.atlassian.com/forums/discussion/2694520/limit-param-maxresults).

These findings justify the limits but do not activate polling in this slice.

## ZDD rollout and rollback

Rollout is zero-downtime because the schema change is additive, defaults to
`issue_sources: []`, admits only `mode: shadow`, and has no daemon or event-loop
consumer. Operators can deploy the code first, then validate one declaration,
then compare its immutable plan with the intended provider scope. No API quota,
external state, worker capacity, file handle, or socket is consumed during the
shadow phase.

Rollback is configuration-only: remove the `issue_sources` key or set it to
`[]`. The legacy `issues` block remains independent and retains its prior
defaults and environment behavior. Because this slice creates no database
rows, cursors, provider objects, processes, or write-back state, rollback has
no cleanup or data migration step.

Activation requires a separate reviewed change with provider lifecycle,
network and filesystem policy, observable bounded polling, deduplication,
write-back authorization, failure isolation, and its own ZDD rollback proof.
