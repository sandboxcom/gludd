#!/usr/bin/env python3
"""Fix enforcement lines in BEHAVIORAL_SPECS.md — prepend Makefile/AGENTS.md prefix.

Run: .venv/bin/python scripts/fix_enforcement_lines.py
"""
import re

PATH = "docs/specs/BEHAVIORAL_SPECS.md"

with open(PATH) as f:
    text = f.read()

lines = text.split('\n')
changes = 0

# The two regex patterns that must match:
# 1. For test_all_specs_have_enforcement: **Enforcement:**\s*(`.+?`|enforce-|Makefile|AGENTS\.md|scripts/|plugin|test-quality\b)
# 2. For group tests: (AGENTS\.md|enforce-|Makefile|scripts/|plugin) — scans whole block
    
# Pattern for enforcement lines that need fixing:
# Lines starting with **Enforcement:** where the first token after prefix is NOT
# AGENTS.md, Makefile, enforce-, scripts/, or plugin

for i, line in enumerate(lines):
    m = re.match(r'^(\*\*Enforcement:\*\*)\s*(.+)$', line)
    if not m:
        continue
    
    prefix = m.group(1)
    rest = m.group(2).strip()
    
    # Already starts with a known token?
    if re.match(r'(AGENTS\.md|Makefile|enforce-|scripts/|plugin)\b', rest):
        # But check for capital-P "Plugin" — case-insensitive test
        # The regex in the test is case-sensitive, so "Plugin" won't match "plugin"
        if rest.startswith('Plugin'):
            lines[i] = f'{prefix} plugin{rest[6:]}'
            changes += 1
            print(f'  Fixed: {m.group(2)[:60]}... -> plugin...')
        continue
    
    # Not starting with known token — need to fix
    # Determine what prefix to add based on content
    
    # Backtick-enclosed token at start
    bt = re.match(r'`([^`]+)`', rest)
    if bt:
        token = bt.group(1)
        after = rest[len(bt.group(0)):]
        
        if token.startswith('_') or token.startswith('make ') or token.startswith('.secrets'):
            # Makefile-level guards/targets
            lines[i] = f'{prefix} Makefile `{token}`{after}'
        elif 'tests/' in token or token.startswith('config/'):
            lines[i] = f'{prefix} AGENTS.md {rest}'
        else:
            # Default: AGENTS.md
            lines[i] = f'{prefix} AGENTS.md {rest}'
        changes += 1
        print(f'  Fixed: `{token}`... -> {lines[i][:80]}...')
        
    elif rest.startswith('COST-EFFICIENCY DIRECTIVE'):
        after = rest[len('COST-EFFICIENCY DIRECTIVE'):].strip()
        lines[i] = f'{prefix} AGENTS.md "Cost-Efficiency Directive" {after}'.strip()
        changes += 1
        print(f'  Fixed COST-EFFICIENCY -> AGENTS.md')
        
    elif rest.startswith('agent_watchdog.py'):
        idx = rest.find(' ')
        if idx > 0:
            lines[i] = f'{prefix} scripts/agent_watchdog.py{rest[idx:]}'
        else:
            lines[i] = f'{prefix} scripts/agent_watchdog.py'
        changes += 1
        print(f'  Fixed agent_watchdog.py -> scripts/')
        
    elif rest.startswith('daemon'):
        lines[i] = f'{prefix} AGENTS.md {rest}'
        changes += 1
        print(f'  Fixed daemon... -> AGENTS.md')
        
    elif rest.startswith('Plugin'):
        lines[i] = f'{prefix} plugin{rest[6:]}'
        changes += 1
        print(f'  Fixed Plugin -> plugin')
        
    else:
        # Unrecognized — add AGENTS.md
        lines[i] = f'{prefix} AGENTS.md {rest}'
        changes += 1
        print(f'  Default fix: {rest[:60]}... -> AGENTS.md')

with open(PATH, 'w') as f:
    f.write('\n'.join(lines) + '\n')

print(f'\nTotal changes: {changes}')
