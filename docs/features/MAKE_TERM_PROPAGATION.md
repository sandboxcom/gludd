# Make child terminal propagation

Status: implemented in the root Makefile and behavior-tested. Release evidence
is tracked in `TASKS.md` as S83.53.

## Problem

The Makefile unconditionally exported `TERM=dumb` to every child process. That
made non-interactive logs predictable, but it also overwrote capable caller
values such as `screen-256color`. TUI-aware tests and developer commands then
lost terminfo capabilities even when the parent terminal had declared them.

At the opposite boundary, an empty, `unknown`, or `dumb` value does not provide
the terminfo entry expected by the repository's terminal-aware test tools. A
deterministic fallback is required without clobbering a real caller choice.

## Behavioral contract

1. Empty, `unknown`, and `dumb` values are promoted to `xterm-256color` for
   Make child processes.
2. Any other caller-selected terminal value is preserved byte for byte.
3. The selected value is exported once and inherited by recursive Make targets.
4. The fallback does not allocate a pseudo-terminal or change stdin/stdout TTY
   identity. Programs must still use stream capability checks for interactive
   behavior.
5. The behavior is deterministic in local shells, isolated workers, and CI; it
   creates no per-project terminal process or global state.

## Zero-downtime, security, and resource boundary

This is build and test environment propagation only. It changes no application
API, daemon, port, database, deployment, or persistent runtime configuration.
Existing services remain untouched, and rollback is one Makefile conditional.

No untrusted command text is evaluated. The value is selected by Make from a
fixed fallback or the inherited environment and then exported. The change adds
no worker, cache, file, network call, or disk usage.

## Practitioner evidence

A Cursor forum report describes agent terminals losing colors because the
parent supplied `TERM=dumb`; the reported correction was `xterm-256color`.
That is the same under-declared-agent boundary covered by the fallback:

- [Cursor forum: colors not working on agent terminal](https://forum.cursor.com/t/colors-not-working-on-agent-terminal/153088)

A long-lived GitHub CLI issue documents the opposite failure: emitting terminal
control sequences while ignoring a genuinely dumb terminal. Maintainers note
that `TERM=dumb` should be treated as non-interactive. Gludd therefore preserves
all capable caller values and does not use `TERM` to fabricate TTY identity:

- [cli/cli issue #5721](https://github.com/cli/cli/issues/5721)

A prompt-toolkit user report further demonstrates that piped input and terminal
identity are separate concerns. The Make fallback never reopens or substitutes
stdin:

- [prompt-toolkit issue #1943](https://github.com/prompt-toolkit/python-prompt-toolkit/issues/1943)

## Verification

- `tests/unit/test_makefile_syntax.py` asserts fallback promotion and exact
  preservation of a capable caller value.
- The same suite validates parsing, target separation, phony declarations,
  variable format, whitespace, and duplicate targets.
- `make validate-makefile` and `make check-make-help` remain the tracked
  structural gates.
- The full release gate remains authoritative for promotion.
