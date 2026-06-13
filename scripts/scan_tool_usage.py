import os, json
from collections import defaultdict

tool_name_counts = defaultdict(int)
bash_command_counts = defaultdict(int)
jsonl_files = []

for root, dirs, files in os.walk(os.path.expanduser('~/.claude/projects/')):
    for fname in files:
        if fname.endswith('.jsonl'):
            jsonl_files.append(os.path.join(root, fname))

for fpath in jsonl_files:
    try:
        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
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

                seen_ids = set()

                def process_items(items_source):
                    if not isinstance(items_source, list):
                        return
                    for item in items_source:
                        if not isinstance(item, dict):
                            continue
                        if item.get('type') == 'tool_use':
                            tid = item.get('id', None)
                            if tid is not None:
                                if tid in seen_ids:
                                    continue
                                seen_ids.add(tid)
                            name = item.get('name', '')
                            if name:
                                tool_name_counts[name] += 1
                                if name == 'Bash':
                                    inp = item.get('input', {})
                                    if isinstance(inp, dict):
                                        cmd = inp.get('command', '')
                                        tokens = cmd.split()
                                        if tokens:
                                            pat = ' '.join(tokens[:2])
                                            bash_command_counts[pat] += 1

                # Rule 1: top-level type == 'tool_use' (direct)
                if obj.get('type') == 'tool_use':
                    tid = obj.get('id', None)
                    if tid is None or tid not in seen_ids:
                        if tid is not None:
                            seen_ids.add(tid)
                        name = obj.get('name', '')
                        if name:
                            tool_name_counts[name] += 1
                            if name == 'Bash':
                                inp = obj.get('input', {})
                                if isinstance(inp, dict):
                                    cmd = inp.get('command', '')
                                    tokens = cmd.split()
                                    if tokens:
                                        pat = ' '.join(tokens[:2])
                                        bash_command_counts[pat] += 1

                # Rules 2 & 3: content array directly on obj
                process_items(obj.get('content'))

                # Also check obj.message.content (Claude Code JSONL wraps in 'message')
                msg = obj.get('message')
                if isinstance(msg, dict):
                    # Check message-level type == 'tool_use'
                    if msg.get('type') == 'tool_use':
                        tid = msg.get('id', None)
                        if tid is None or tid not in seen_ids:
                            if tid is not None:
                                seen_ids.add(tid)
                            name = msg.get('name', '')
                            if name:
                                tool_name_counts[name] += 1
                                if name == 'Bash':
                                    inp = msg.get('input', {})
                                    if isinstance(inp, dict):
                                        cmd = inp.get('command', '')
                                        tokens = cmd.split()
                                        if tokens:
                                            pat = ' '.join(tokens[:2])
                                            bash_command_counts[pat] += 1
                    process_items(msg.get('content'))

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
