import json
import os
from collections import defaultdict

tool_name_counts: defaultdict[str, int] = defaultdict(int)
bash_command_counts: defaultdict[str, int] = defaultdict(int)
jsonl_files: list[str] = []


def _record_tool_use(
    item: dict[str, object],
    seen_ids: set[object],
    tool_counts: defaultdict[str, int],
    bash_counts: defaultdict[str, int],
) -> None:
    """Count a single tool_use entry, de-duping by id within the record."""
    tid = item.get('id')
    if tid is not None:
        if tid in seen_ids:
            return
        seen_ids.add(tid)
    name = str(item.get('name', ''))
    if name:
        tool_counts[name] += 1
        if name == 'Bash':
            inp = item.get('input', {})
            if isinstance(inp, dict):
                cmd = str(inp.get('command', ''))
                tokens = cmd.split()
                if tokens:
                    pat = ' '.join(tokens[:2])
                    bash_counts[pat] += 1


def process_items(
    items_source: object,
    seen_ids: set[object],
    tool_counts: defaultdict[str, int],
    bash_counts: defaultdict[str, int],
) -> None:
    if not isinstance(items_source, list):
        return
    for item in items_source:
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'tool_use':
            _record_tool_use(item, seen_ids, tool_counts, bash_counts)


for root, _dirs, files in os.walk(os.path.expanduser('~/.claude/projects/')):
    for fname in files:
        if fname.endswith('.jsonl'):
            jsonl_files.append(os.path.join(root, fname))

for fpath in jsonl_files:
    try:
        with open(fpath, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue

                seen_ids: set[object] = set()

                # Rule 1: top-level type == 'tool_use' (direct)
                if obj.get('type') == 'tool_use':
                    _record_tool_use(
                        obj, seen_ids, tool_name_counts, bash_command_counts
                    )

                # Rules 2 & 3: content array directly on obj
                process_items(
                    obj.get('content'), seen_ids, tool_name_counts, bash_command_counts
                )

                # Also check obj.message.content (Claude Code JSONL wraps in 'message')
                msg = obj.get('message')
                if isinstance(msg, dict):
                    # Check message-level type == 'tool_use'
                    if msg.get('type') == 'tool_use':
                        _record_tool_use(
                            msg, seen_ids, tool_name_counts, bash_command_counts
                        )
                    process_items(
                        msg.get('content'),
                        seen_ids,
                        tool_name_counts,
                        bash_command_counts,
                    )

    except Exception:
        pass

print(f'Found {len(jsonl_files)} JSONL files')
print()
print('=== ALL TOOL NAME COUNTS (sorted by count desc) ===')
sorted_tools = sorted(tool_name_counts.items(), key=lambda x: (-x[1], x[0]))
for name, cnt in sorted_tools:
    print(f'  {name}: {cnt}')

print()
print('=== TOP 40 BASH COMMAND (first 2 tokens) COUNTS (sorted by count desc) ===')
sorted_bash = sorted(bash_command_counts.items(), key=lambda x: (-x[1], x[0]))
for pat, cnt in sorted_bash[:40]:
    print(f'  {pat}: {cnt}')
