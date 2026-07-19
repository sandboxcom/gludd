#!/usr/bin/env python3
"""Fix YAML block scalar indentation in language role task files.

Add exactly 6 spaces to Python code lines BETWEEN the 'python3 -c "' opener
and the closing '"'. The opener line is already correctly indented and stays
as-is — it sets the YAML block scalar content baseline.
"""
import sys


def fix_file(filepath):
    with open(filepath, 'r') as f:
        lines = f.readlines()

    result = []
    inside_block = False
    for line in lines:
        stripped = line.strip()

        # Detect start of Python block: the 'python3 -c "' line after cmd: |
        if not inside_block and 'python3 -c "' in stripped:
            inside_block = True
            result.append(line)  # opener line stays as-is
            continue

        if inside_block:
            # closing '"' line — add 6 spaces, then exit block
            if stripped == '"':
                result.append('      ' + line)
                inside_block = False
                continue
            # Python code line — add 6 spaces
            result.append('      ' + line)
            continue

        result.append(line)

    with open(filepath, 'w') as f:
        f.writelines(result)
    print(f'Fixed: {filepath}')


for f in sys.argv[1:]:
    fix_file(f)
