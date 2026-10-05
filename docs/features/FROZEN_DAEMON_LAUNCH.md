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
