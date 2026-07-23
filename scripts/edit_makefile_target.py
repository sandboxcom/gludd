#!/usr/bin/env python3
"""edit_makefile_target.py - Add and edit Makefile targets with validation.
Modes: add, extract, validate, replace
"""
from __future__ import annotations
import re, subprocess, sys, os
from pathlib import Path

def _repo_root():
    """Find repo root: walk up 2 levels from script, with CWD fallback."""
    script_dir = Path(__file__).resolve().parent
    repo = script_dir.parent
    if not (repo / 'Makefile').exists():
        repo = script_dir.parents[2] if len(script_dir.parents) > 2 else Path(os.getcwd())
        if not (repo / 'Makefile').exists():
            repo = Path(os.getcwd())
    return repo

REPO_ROOT = _repo_root()
MAKEFILE_PATH = REPO_ROOT / 'Makefile'

HELP_SECTION_ORDER = [
    'Setup', 'Quality', 'Terraform', 'Git', 'Secrets + Security',
    'Release', 'Build + Deploy', 'Ansible', 'CI', 'Git Remote',
    'SearXNG Research Backend', 'Disk', 'Recovery',
]

SECTION_KEYWORDS = {
    'Setup': ['init', 'sync', 'bootstrap', 'install', 'setup', 'dir'],
    'Quality': ['lint', 'typecheck', 'mypy', 'ruff', 'check-type', 'gate', 'qa', 'validate', 'health', 'collect', 'preflight', 'coverage'],
    'Terraform': ['tf', 'terraform'],
    'Git': ['git', 'branch', 'commit', 'merge', 'push', 'pull', 'rebase', 'stash', 'reset', 'tag', 'worktree', 'submodule', 'feature', 'development', 'agent'],
    'Secrets + Security': ['secret', 'security', 'sast', 'sbom', 'audit', 'scan', 'scrub'],
    'Release': ['release', 'version', 'bump', 'deploy'],
    'Build + Deploy': ['build', 'dist', 'container', 'executable', 'package', 'deb', 'vm', 'sandbox'],
    'Ansible': ['ansible', 'playbook', 'molecule'],
    'CI': ['ci-', 'cooldown'],
    'Git Remote': ['remote', 'sandboxcom', 'ship-'],
    'SearXNG Research Backend': ['searx'],
    'Disk': ['disk', 'tmp', 'clean'],
    'Recovery': ['recovery', 'backup', 'restore', 'crash'],
}

def categorize_section(name, description):
    combined = '{} {}'.format(name, description).lower()
    for section in HELP_SECTION_ORDER:
        for kw in SECTION_KEYWORDS.get(section, []):
            if kw.lower() in combined:
                return section
    return 'New Targets'

def extract_target_definition(makefile_path, target_name):
    path = makefile_path or MAKEFILE_PATH
    lines = path.read_text(encoding='utf-8').splitlines()
    target_re = re.compile(r'^{}:\s*$'.format(re.escape(target_name)))
    for i, line in enumerate(lines):
        if target_re.match(line):
            start = i
            while start > 0 and (lines[start - 1].startswith('#') or lines[start - 1].strip() == ''):
                start -= 1
            end = i + 1
            while end < len(lines):
                nxt = lines[end]
                if (nxt.startswith('\t') or nxt.startswith('	@')
                    or nxt.startswith('if') or nxt.startswith('else')
                    or nxt.startswith('endif') or nxt.strip() == ''):
                    if nxt.strip() == '' and end > i + 1:
                        if end + 1 < len(lines) and lines[end + 1].strip() == '':
                            break
                    end += 1
                else:
                    break
            return '\n'.join(lines[start:end]).strip() + '\n'
    return None

def make_echo_cmd(name, description):
    return '@echo "{}: {}"'.format(name, description)

def make_new_block(name, description):
    return '# {}\n{}:\n\t{}\n'.format(description, name, make_echo_cmd(name, description))

def _find_section_insert_idx(lines, section):
    escaped_section = re.escape(section.lower())
    section_re = re.compile(r'\b' + escaped_section + r'\b')
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith('# --- ') and stripped.endswith(' ---'):
            inner = stripped[5:-4].strip().lower()
            if section_re.search(inner):
                insert_idx = i + 1
                while insert_idx < len(lines) and lines[insert_idx].strip().startswith('#'):
                    insert_idx += 1
                if insert_idx < len(lines) and lines[insert_idx].strip() == '':
                    insert_idx += 1
                return insert_idx
    return None

def insert_target(makefile_path, name, description, section):
    path = makefile_path or MAKEFILE_PATH
    content = path.read_text(encoding='utf-8')
    if section == 'New Targets' or section not in HELP_SECTION_ORDER:
        return insert_at_end(path, name, description)
    lines = content.splitlines()
    insert_idx = _find_section_insert_idx(lines, section)
    if insert_idx is None:
        return insert_at_end(path, name, description)
    block_lines = make_new_block(name, description).split('\n')
    for bl in reversed(block_lines):
        lines.insert(insert_idx, bl)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    update_help_block(path, name, description, section)
    return True

def insert_at_end(path, name, description):
    content = path.read_text(encoding='utf-8')
    header = '# --- New Targets (auto-categorized add-target) ---'
    block = make_new_block(name, description)
    if header in content:
        idx = content.index(header) + len(header)
        nl = content.index('\n', idx) if '\n' in content[idx:] else len(content)
        path.write_text(content[:nl + 1] + '\n' + block + '\n' + content[nl + 1:], encoding='utf-8')
    else:
        path.write_text(content.rstrip('\n') + '\n\n' + header + '\n' + block + '\n', encoding='utf-8')
    update_help_block(path, name, description, 'New Targets')
    return True

def update_help_block(path, name, description, section):
    content = path.read_text(encoding='utf-8')
    lines = content.splitlines()
    help_start = None
    help_end = None
    in_help = False
    for i, ln in enumerate(lines):
        if ln.strip() == 'help:':
            help_start = i
            in_help = True
            continue
        if in_help:
            if ln.strip() == '' and i > (help_start or 0) + 2:
                peek = i + 1
                if peek < len(lines) and not lines[peek].startswith('\t@echo'):
                    help_end = i
                    break
            if i == len(lines) - 1:
                help_end = i + 1
    if help_start is None or help_end is None:
        return
    section_str = '  --- {} ---'.format(section)
    section_line_idx = None
    for i in range(help_start, help_end):
        if section_str in lines[i]:
            section_line_idx = i
            break
    desc_line = '\t@echo "  {:<24s}{}"'.format(name, description)
    if section_line_idx is None:
        if section == 'New Targets':
            before_idx = help_end - 1
            while before_idx > help_start and (lines[before_idx].strip() == '' or 'Complete Target Index' in lines[before_idx]):
                before_idx -= 1
            insert_at = before_idx + 1
            new_help = [
                '\t@echo "  --- New Targets ---"',
                '\t@echo ""',
                desc_line,
                '\t@echo ""',
            ]
            for j, hl in enumerate(new_help):
                lines.insert(insert_at + j, hl)
    else:
        lines.insert(section_line_idx + 1, desc_line)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

def validate_makefile(makefile_path, target_name):
    path = makefile_path or MAKEFILE_PATH
    ok = True
    if target_name:
        result = subprocess.run(['make', '-n', target_name], capture_output=True, text=True, cwd=str(path.parent))
        if result.returncode != 0:
            print('VALIDATE FAIL: make -n {}'.format(target_name), file=sys.stderr)
            print(result.stderr.strip(), file=sys.stderr)
            ok = False
        else:
            print('VALIDATE OK: make -n {}'.format(target_name))
    result = subprocess.run(['make', 'check-duplicate-targets'], capture_output=True, text=True, cwd=str(path.parent))
    if result.returncode != 0:
        print('VALIDATE FAIL: make check-duplicate-targets', file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        ok = False
    else:
        print(result.stdout.strip())
    return ok

def main():
    import argparse
    p = argparse.ArgumentParser(description='Add or edit Makefile targets')
    sp = p.add_subparsers(dest='mode', required=True)
    ap = sp.add_parser('add')
    ap.add_argument('--name', required=True)
    ap.add_argument('--description', required=True)
    ap.add_argument('--section', default='')
    ep = sp.add_parser('extract')
    ep.add_argument('--name', required=True)
    vp = sp.add_parser('validate')
    vp.add_argument('--name', required=True)
    rp = sp.add_parser('replace')
    rp.add_argument('--name', required=True)
    rp.add_argument('--file', required=True)
    args = p.parse_args()
    if args.mode == 'add':
        section = args.section or categorize_section(args.name, args.description)
        if extract_target_definition(None, args.name) is not None:
            print('ERROR: Target {!r} already exists in Makefile.'.format(args.name), file=sys.stderr)
            return 1
        print('Adding target {!r} to section {!r}...'.format(args.name, section))
        if insert_target(None, args.name, args.description, section):
            if validate_makefile(None, args.name):
                print('Target {!r} added and validated.'.format(args.name))
                return 0
            print('WARNING: Target added but validation failed.', file=sys.stderr)
            return 1
        return 1
    elif args.mode == 'extract':
        definition = extract_target_definition(None, args.name)
        if definition:
            print(definition)
            return 0
        print('Target {!r} not found in Makefile.'.format(args.name), file=sys.stderr)
        return 1
    elif args.mode == 'validate':
        return 0 if validate_makefile(None, args.name) else 1
    elif args.mode == 'replace':
        new_def = Path(args.file).read_text(encoding='utf-8')
        old_def = extract_target_definition(None, args.name)
        if old_def is None:
            print('Target {!r} not found.'.format(args.name), file=sys.stderr)
            return 1
        content = MAKEFILE_PATH.read_text(encoding='utf-8')
        if old_def.strip() not in content:
            print('Could not locate current definition of {!r}.'.format(args.name), file=sys.stderr)
            return 1
        content = content.replace(old_def.strip(), new_def.strip(), 1)
        MAKEFILE_PATH.write_text(content, encoding='utf-8')
        if validate_makefile(None, args.name):
            print('Target {!r} replaced and validated.'.format(args.name))
            return 0
        print('WARNING: Target replaced but validation failed.', file=sys.stderr)
        return 1
    return 1

if __name__ == '__main__':
    sys.exit(main())
