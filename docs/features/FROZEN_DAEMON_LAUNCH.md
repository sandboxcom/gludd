# Frozen Daemon Launch Contract

The standalone Gludd executable must start its daemon without assuming a
host-installed `gunicorn` command. A frozen process re-executes
`sys.executable` with a private bootstrap flag; the bootstrap removes that flag
and invokes Gunicorn's bundled Python entry point. Source checkouts retain the
ordinary `gunicorn` command path.

Frozen children inherit stdout and stderr so startup failures remain observable.
Source-mode background children keep the established quiet default. The private
flag is accepted only when `sys.frozen` is true, preventing ordinary CLI users
from reaching the internal bootstrap.

The release smoke invokes the same blocking public contract as operators:
`gludd daemon --host 127.0.0.1 --port 8000`. There is intentionally no nested
`start` verb. It probes the public `/healthz` liveness route, checks the owned
process on every attempt, reports its real early-exit status, and always reaps
the process. This closes the 2026-08-21 GHE incident where an obsolete
`gludd daemon start` invocation failed in argument parsing after the binary had
built successfully.

## Practitioner evidence

PyInstaller users have repeatedly found that frozen subprocesses cannot rely on
external console scripts. In
[PyInstaller discussion #8090](https://github.com/orgs/pyinstaller/discussions/8090),
maintainers recommend invoking the frozen application's `sys.executable` and
using an explicit argument to select the subprocess code path. The older
[PyInstaller issue #1726](https://github.com/pyinstaller/pyinstaller/issues/1726)
also records why `sys.executable`, rather than `argv[0]`, is the reliable
frozen executable location.

## ZDD, rollback, and security

This change has no schema, state, or network migration. Deployment remains
zero-downtime because source and frozen launch paths coexist. Rollback is the
previous executable artifact. The bootstrap flag is private, frozen-only, and
passes Gunicorn arguments as an argv list; it introduces no shell parsing.
Inherited frozen-child logs provide release observability without changing
source-mode background logging.

## Native observer packaging repair (2026-11-07 UTC)

Diagnostic source5a203e9b41644378247969a92b89b01bd878cbad reaches the actual
[Build42375234504](https://github.com/sandboxcom/gludd/actions/runs/42375234504).
Linux job128414740647 fails its warning-graph audit after building; Molecule1
job128414740766 independently reports the same missing stream_command edge from
general_ludd.git_automation.locking. Native job-specific context establishes both
failures; successful compilation alone is not accepted packaging.

The spec repair adds the existing physical scripts search path and explicit
native stream_command/mutex imports. It changes no scanner, reviewed-warning
allowlist, platform exclusion, workflow condition or audit outcome. Two additive
controls fail before repair, then pass: one resolves the actual import origin;
the other compiles the real spec with only its build API doubled and inspects
the actual Analysis call. Complete ten-owner acceptance plus the logical-Makefile
guard passes187 each: Python3.11 takes27.37s/native28.798s; Python3.12 takes
31.12s/native32.630s. Label v012-linux-observer-complete retains exact RIDs
v012-linux-observer-complete-ten311-root-20261107 and
v012-linux-observer-complete-ten312-root-20261107. Sessions are drained and
observed-status confirms passed; each trace has187 pairs and zero issues.

Root reopened the retained
[PyInstaller8090 discussion](https://github.com/orgs/pyinstaller/discussions/8090)
on 2026-11-07. The2023 user report and maintainer replies distinguish bundled
Python code from an assumed external console executable, and describe re-executing
the frozen application's own entrypoint. That supports the existing daemon-launch
boundary; it does not certify our archive or a missing external controller.
The [PyInstaller troubleshooting guide](https://pyinstaller.org/en/stable/when-things-go-wrong.html)
separately describes inspecting Analysis warnings/import graphs and correcting
module discovery. Those reports guide diagnosis, not an audit exemption.

The separate locking repair preserves source search paths and uses the existing
trusted observer import owner; its native RED and receiving have their own scope.
New Linux/archive execution, terminal exact-SHA hosted success, normal branch
promotion, ZDD deployment and the complete signed release remain required.
