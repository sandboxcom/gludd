# Feature: `gludd chat` — Interactive Agent Chat REPL

**Status: IMPLEMENTED** | **Created: 2026-07-14** | **Target: v0.1.0-beta.2**

> **Implementation complete 2026-07-16.** Phases P1–P5 all delivered.
> Verified by 115 passing tests across unit, integration, and E2E suites.

## 1. Overview

`gludd chat` drops the user into a local, synchronous REPL that talks to an AI
agent (OpenAI/Anthropic/OpenRouter via existing connector layer). In REPL mode,
the user types prompts, gets streaming responses, and sees syntax-highlighted
code blocks. With `--eval PROMPT`, it runs a single turn and prints the
response (no REPL, exit 0). Both modes bypass the daemon's dispatch/todo queue
— responses are immediate, foreground, and no background jobs are created.

## 2. Architecture

- **ChatSession** (`src/general_ludd/chat/session.py`): owns the event loop,
  manages conversation history as a `list[dict[str,str]]` (OpenAI-format
  messages), and delegates I/O to `MessageFormatter`. Reads user input via
  `prompt_toolkit` `PromptSession`.
- **MessageFormatter** (`src/general_ludd/chat/formatter.py`): scans
  incoming text for fenced code blocks (triple-backtick), extracts language
  tags, and applies syntax highlighting via `pygments`. Renders via `rich`
  `Syntax` for terminal-safe color.
- **Direct call path**: ChatSession calls the model provider directly through
  `general_ludd.connectors` — never through the daemon dispatch pipeline.
- **Streaming**: model responses produce `AsyncIterator[str]` chunks consumed
  by ChatSession and written to `stdout` chunk-by-chunk.

## 3. CLI Interface

```text
gludd chat                         # interactive REPL
gludd chat --eval PROMPT           # oneshot (non-interactive)
gludd chat --eval PROMPT --model MODEL  # select provider/model
gludd chat --system-prompt TEXT    # override system message
gludd chat --history FILE          # load/save conversation history
```

- `--eval`: if present, ChatSession runs one turn and prints response, exits.
  Otherwise, enters REPL.
- `--model`: model profile name (maps to existing agent model infrastructure).
- `--system-prompt`: override default. Default: agent has access to ansible
  and terraform for advanced tasks.
- `--history`: path to JSON-lines conversation file.

## 4. Implementation Plan

| Phase | Scope | Files |
|-------|-------|-------|
| P1 | ChatSession class + --eval mode. Hard-coded openai client, raw text output. | `chat/session.py`, `chat/__init__.py`, CLI registration |
| P2 | MessageFormatter with pygments + rich code-block highlighting. | `chat/formatter.py` |
| P3 | REPL mode via PromptSession. Readline keybindings (arrows, backspace, Ctrl-C cancel, Ctrl-D exit). Streaming output. | `chat/session.py` (extend) |
| P4 | Multi-model via --model flag, ansible/terraform context injection. | `chat/session.py` (extend) |
| P5 | History file (~/.cache/gludd/chat_history), persistent context, session resume. | `chat/session.py` (extend) |

## 5. Files

| Action | Path |
|--------|------|
| Create | `src/general_ludd/chat/__init__.py` |
| Create | `src/general_ludd/chat/session.py` |
| Create | `src/general_ludd/chat/formatter.py` |
| Modify | `src/general_ludd/cli.py` (add chat subparser + handler) |
| Create | `tests/unit/test_chat_session.py` |
| Create | `tests/unit/test_chat_formatter.py` |
| Create | `tests/integration/test_chat_cli.py` |

## 6. Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| prompt_toolkit | >=3.0.0 | REPL input, readline keys, Ctrl-C handling |
| pygments | >=2.0.0 | Code-block syntax highlighting |
| rich | >=13.0.0 (existing) | Syntax rendering, terminal colors |
| httpx | >=0.28.0 (existing) | Streaming HTTP for provider API |

## 7. Test Plan

- **Unit**: `ChatSession._parse_args()` extracts model/daemon-url correctly.
  `MessageFormatter` detects code blocks, applies correct lexer by language tag.
- **Integration**: mock provider returns known response; verify ChatSession
  reproduces it identically. Mock streaming chunks; verify output order.
- **E2E**: `subprocess.run(["gludd", "chat", "--eval", "say hi"])` exits 0
  with non-empty stdout.
