from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PREFIX = "if (isSubagent()) return"
EXACT_PREFIX = "if (process.env.OPENCODE_SUBAGENT === \"1\") return"


def exact_guard(indent: str, tail: str) -> str:
    stripped = tail.strip()
    if stripped.startswith("output"):
        return f"{indent}{EXACT_PREFIX} output"
    if stripped.startswith(";"):
        return f"{indent}{EXACT_PREFIX};"
    return f"{indent}{EXACT_PREFIX}"


def transform(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    out: list[str] = []
    inserted = 0
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith(PREFIX):
            prev = out[-1].strip() if out else ""
            if not prev.startswith(EXACT_PREFIX):
                tail = stripped[len(PREFIX):]
                out.append(exact_guard(indent, tail))
                inserted += 1
        out.append(line)
    ending = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + ending, inserted


def main() -> int:
    total = 0
    for path in sorted(PLUGIN_DIR.glob("enforce-*.ts")):
        old = path.read_text()
        new, inserted = transform(old)
        if inserted:
            path.write_text(new)
            total += inserted
            print(f"{path.relative_to(ROOT)}: inserted {inserted}")
    print(f"exact subagent guards inserted: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
