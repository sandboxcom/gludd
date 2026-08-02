# CLI self-test compatibility

## Contract

`gludd test self` is the canonical self-test command. Existing scripts may keep
using `gludd selftest`; both spellings dispatch `_cmd_selftest`, accept the same
`--daemon-url` option, and default to `http://localhost:8000`. The top-level
spelling is a compatibility surface, not a second implementation.

One shared parser configurator owns the option and handler registration so the
two entry points cannot silently drift. Focused structural and E2E tests compare
their parsed namespaces, handler dispatch, custom URL handling, and help output.

## Upstream evidence

The [official `argparse` subcommand documentation](https://docs.python.org/3/library/argparse.html#sub-commands)
recommends `set_defaults()` for dispatch and documents aliases only for names on
the *same* subparser action. Since `selftest` is top-level while `test self` is
nested, Gludd registers two parsers and shares their configuration rather than
depending on private parser internals.

This is a long-lived source of user confusion. A
[2023 Python forum discussion](https://discuss.python.org/t/argparser-subcommands-function-as-a-feature-not-a-workaround/30207)
notes that function dispatch through subparsers is already the documented
pattern, while a
[2014–2021 CPython user issue](https://github.com/python/cpython/issues/66246)
shows why unsupported parser nesting and private implementation shortcuts age
poorly. The compatibility parser therefore uses only public `argparse` APIs and
keeps both command paths under behavioral tests.

## Regression evidence

Before restoration, the focused CLI slice had four failures: both alias dispatch
tests exited with parser error 2, and the live self-test success/offline paths
never reached their handler. The canonical nested parser itself remained valid.
