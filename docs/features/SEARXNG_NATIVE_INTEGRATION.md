# Native SearXNG Integration

## Decision

Gludd uses the official SearXNG Python application directly for controller-local
search. `NativeSearxRuntime` imports `searx.webapp`, binds its WSGI application,
checks `/healthz`, and calls the documented `/search` JSON API without opening a
socket or starting a child process.

HTTP remains available only through the explicit `RemoteSearxAdapter` or the
Ansible `transport: remote` option. A URL no longer silently changes a local
operation into a remote one.

## Upstream Contract

The integration is deliberately thin and follows these upstream entry points:

- The official source distribution installs the import package `searx` and the
  `searxng-run = searx.webapp:run` console entry point in
  [upstream `setup.py`](https://github.com/searxng/searxng/blob/master/setup.py).
- [`searx.webapp`](https://github.com/searxng/searxng/blob/master/searx/webapp.py)
  exposes the WSGI application, `/healthz`, and `/search` routes.
- The [Search API documentation](https://github.com/searxng/searxng/blob/master/docs/dev/search_api.rst)
  defines the query parameters and JSON response format used here.
- The [official installation guide](https://docs.searxng.org/admin/installation.html)
  remains the authority for installing and upgrading the server package.

The official server is not installed from the `searxng` project on PyPI. That
project name is currently used by an unrelated third-party client. Gludd probes
for `searx.webapp` and fails with an installation instruction instead of running
`pip` at runtime or accidentally installing a different project.

## Python Lifecycle

```python
from general_ludd.searx import NativeSearxRuntime

with NativeSearxRuntime(namespace="trip-planner") as searx:
    response = searx.search(
        "accessible hotels near the conference",
        categories=("general",),
        max_results=10,
    )
```

Startup creates and health-checks the direct WSGI client before publishing it.
Restart creates and health-checks a replacement before closing the current
client. If replacement health fails, the current client remains available. Stop
is idempotent and closes only the client owned by that runtime.

The in-process upstream application is a module-global singleton. Consequently,
one controller process should use one SearXNG settings file. Run separate Gludd
controller processes for projects that need different upstream configurations;
the default namespace and configuration directory are project-scoped so those
processes do not collide.

## Ansible Boundary

The travel collection uses action plugins so the official Python package stays
on the controller. No Terraform directory, Docker task, raw `uri` task, or
managed-host package installation is required.

```yaml
- name: Keep a native runtime available
  general_ludd.travel.searxng_instance:
    state: started
    namespace: trip-planner

- name: Search through that runtime
  general_ludd.travel.searxng_search:
    query: accessible museums near Union Square
    category: activities
    namespace: trip-planner
    max_results: 8
  register: museums
```

`searxng_instance` supports `started`, `stopped`, `restarted`, and `status`.
Repeated start and stop operations are idempotent. Check mode predicts lifecycle
changes without importing SearXNG, creating configuration, or making a search.
Search is read-only and also performs no work in check mode.

Remote compatibility must be requested explicitly:

```yaml
- name: Search an operator-managed remote service
  general_ludd.travel.searxng_search:
    query: rail disruption updates
    transport: remote
    remote_url: https://search.internal.example
```

The retired `searxng_url`, `project_path`, and `terraform_project_path` glue is
rejected rather than interpreted ambiguously.

## Security and Resource Isolation

- Local search opens no listener and creates no child process. The runtime
  reports `process_pid: null` and a non-network `searx+python://` identity.
- Namespaces are bounded to 64 safe characters. The default includes a digest of
  the project path or honors `GLUDD_RESOURCE_NAMESPACE`.
- Generated configuration directories are mode `0700`; settings files are mode
  `0600`, written atomically, and preserve the existing secret on repeat runs.
- Symlinked, non-regular, group-writable, and world-writable settings files are
  rejected before the upstream package is imported.
- Queries reject NUL bytes and excessive length; result counts are bounded.
  Search exceptions do not echo the query, which may contain sensitive terms.
- Remote HTTP retains connector URL validation and SSRF protections, but only on
  the explicitly selected compatibility path.

These boundaries let independent Gludd controller processes coexist without
port, PID-file, configuration, or process-name collisions.

## Practitioner Findings and Operational Limits

The upstream community has repeatedly encountered several operational classes
that a lifecycle wrapper cannot hide:

- JSON is opt-in in `search.formats`; otherwise API callers see HTTP errors. The
  configuration trap is documented in
  [discussion #1789](https://github.com/searxng/searxng/discussions/1789).
- Source installations can drift as system and Python dependencies change, as
  shown by [discussion #3106](https://github.com/searxng/searxng/discussions/3106)
  and [issue #3896](https://github.com/searxng/searxng/issues/3896). This is why
  Gludd does not implement a second installer inside application startup.
- Engine failures, upstream throttling, CAPTCHAs, and timeouts recur across the
  old and current communities; see
  [Searx issue #3042](https://github.com/searx/searx/issues/3042),
  [SearXNG discussion #5651](https://github.com/searxng/searxng/discussions/5651),
  [discussion #6492](https://github.com/searxng/searxng/discussions/6492), and
  [issue #5733](https://github.com/searxng/searxng/issues/5733). Callers must
  inspect `unresponsive_engines` and should not treat an empty result set as
  proof that the upstream sources had no matching content.

SearXNG still performs outbound network work and can inherit the latency and
availability of every selected engine. Native integration removes the local HTTP
hop; it does not remove engine policy, egress, rate-limit, or bot-detection
constraints.

## Zero-Downtime Changes and Rollback

For an in-process restart, Gludd validates a replacement WSGI client before
retiring the current client. For a transport migration, bring up and health-check
the remote instance first, change a canary task to `transport: remote`, and then
roll the explicit setting through the remaining callers. Rollback is the inverse
configuration change; the native path does not depend on remote state.

Configuration changes that require a fresh upstream module import should be
rolled through a new controller process. Start and health-check the replacement
controller before draining the old one, preserving the same zero-downtime
handoff at the process boundary.
