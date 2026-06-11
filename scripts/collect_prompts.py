"""Collect system prompts from open-source coding agents.

Fetches system prompts from GitHub repos of known coding agents and stores
them as YAML files in config/prompt_profiles/.

Usage:
    python scripts/collect_prompts.py [--output-dir DIR] [--source SOURCE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
import urllib.request
import yaml
from pathlib import Path

SOURCES: dict[str, dict[str, str]] = {
    "aider": {
        "repo": "paul-gauthier/aider",
        "description": "AI pair programming in your terminal",
        "paths": [
            "aider/coders/editblock_prompts.py",
            "aider/coders/wholefile_prompts.py",
            "aider/coders/editblock_func_prompts.py",
            "aider/coders/udiff_prompts.py",
            "aider/coders/help_prompts.py",
        ],
        "extract": "python_class_var",
        "var_names": ["main", "system", "SYSTEM"],
    },
    "openhands": {
        "repo": "All-Hands-AI/OpenHands",
        "description": "Platform for software development agents",
        "paths": [
            "openhands/controller/agent.py",
            "openhands/memory/context_window.py",
            "agenthub/codeact_agent/prompt.py",
        ],
        "extract": "python_string",
    },
    "swe_agent": {
        "repo": "princeton-nlp/SWE-agent",
        "description": "Agent for automated software engineering",
        "paths": [
            "sweagent/agent/default_prompts.yaml",
            "sweagent/agent/commands/",
        ],
        "extract": "yaml_file",
    },
    "cline": {
        "repo": "cline/cline",
        "description": "Autonomous coding agent for VS Code",
        "paths": [
            "src/core/prompts/system.ts",
            "src/core/prompts/tools.ts",
        ],
        "extract": "typescript_string",
    },
    "aider_chat": {
        "repo": "paul-gauthier/aider",
        "description": "Aider chat-level system prompts",
        "paths": [
            "aider/coders/chat_prompts.py",
        ],
        "extract": "python_class_var",
        "var_names": ["main", "system", "SYSTEM"],
    },
}

GITHUB_RAW = "https://raw.githubusercontent.com"
GITHUB_API = "https://api.github.com/repos"


def _fetch_url(url: str, timeout: int = 30) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "general-ludd/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except Exception as exc:
        print(f"  WARN: Failed to fetch {url}: {exc}", file=sys.stderr)
        return None


def _extract_python_strings(content: str, var_names: list[str]) -> list[str]:
    prompts: list[str] = []
    patterns = [
        re.compile(
            r'(?:SYSTEM|system|main|SYSTEM_PROMPT)\s*=\s*(?:f?"""|f?\'\'\')'
            r'(.*?)'
            r'(?:"""|\'\'\')',
            re.DOTALL,
        ),
        re.compile(
            r'["\'](?:system|SYSTEM)["\']\s*:\s*(?:f?"""|f?\'\'\')'
            r'(.*?)'
            r'(?:"""|\'\'\')',
            re.DOTALL,
        ),
    ]
    for pattern in patterns:
        for match in pattern.findall(content):
            text = match.strip()
            if len(text) > 100:
                prompts.append(text)
    return prompts


def _extract_yaml_prompts(content: str) -> list[str]:
    try:
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            for key in ("system_prompt", "system", "prompt", "template"):
                if key in data and isinstance(data[key], str) and len(data[key]) > 100:
                    return [data[key]]
            for key in ("prompts", "templates"):
                if key in data and isinstance(data[key], list):
                    return [
                        p for p in data[key]
                        if isinstance(p, (str, dict))
                    ]
    except yaml.YAMLError:
        pass
    return []


def _extract_ts_strings(content: str) -> list[str]:
    prompts: list[str] = []
    patterns = [
        re.compile(
            r'(?:systemPrompt|system_prompt|SYSTEM_PROMPT)\s*[=:]\s*`'
            r'(.*?)'
            r'`',
            re.DOTALL,
        ),
        re.compile(
            r'(?:systemPrompt|system_prompt|SYSTEM_PROMPT)\s*[=:]\s*(?:`|\'|")'
            r'(.*?)'
            r'(?:`|\'|")',
            re.DOTALL,
        ),
    ]
    for pattern in patterns:
        for match in pattern.findall(content):
            text = match.strip()
            if len(text) > 100:
                prompts.append(text)
    return prompts


def _extract_prompts(
    content: str, extract_type: str, var_names: list[str] | None = None
) -> list[str]:
    if extract_type == "python_string" or extract_type == "python_class_var":
        return _extract_python_strings(content, var_names or [])
    elif extract_type == "yaml_file":
        return _extract_yaml_prompts(content)
    elif extract_type == "typescript_string":
        return _extract_ts_strings(content)
    return []


def _list_dir(repo: str, path: str) -> list[str]:
    url = f"{GITHUB_API}/{repo}/contents/{path}"
    content = _fetch_url(url)
    if content is None:
        return []
    try:
        entries = json.loads(content)
        if isinstance(entries, list):
            return [
                e["path"]
                for e in entries
                if isinstance(e, dict) and e.get("type") == "file"
            ]
    except json.JSONDecodeError:
        pass
    return []


def collect_source(
    source_name: str,
    source_info: dict[str, str],
    output_dir: Path,
) -> list[Path]:
    repo = source_info["repo"]
    paths = source_info["paths"]
    extract_type = source_info["extract"]
    var_names = source_info.get("var_names", [])
    collected: list[Path] = []

    resolved_paths: list[str] = []
    for path in paths:
        if path.endswith("/"):
            resolved_paths.extend(_list_dir(repo, path.rstrip("/")))
        else:
            resolved_paths.append(path)

    for path in resolved_paths:
        url = f"{GITHUB_RAW}/{repo}/HEAD/{path}"
        print(f"  Fetching {url}")
        content = _fetch_url(url)
        if content is None:
            continue

        prompts = _extract_prompts(content, extract_type, var_names)
        for i, prompt_text in enumerate(prompts):
            if len(prompt_text) < 50:
                continue
            stem = Path(path).stem.replace(".", "_")
            profile_name = f"{source_name}__{stem}"
            if i > 0:
                profile_name += f"_{i}"
            profile_name = re.sub(r"[^a-z0-9_]", "_", profile_name.lower())

            profile_data = {
                "name": profile_name,
                "source": source_name,
                "source_url": f"https://github.com/{repo}/blob/HEAD/{path}",
                "description": source_info.get("description", ""),
                "task_types": [],
                "tags": [source_name, "collected"],
                "version": "latest",
                "prompt_text": textwrap.dedent(prompt_text),
            }

            out_path = output_dir / f"{profile_name}.yml"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w") as f:
                license_info = source.get("license", "Apache-2.0")
                repo_url = f"https://github.com/{source['repo']}"
                f.write(
                    f"# Source: {repo_url}\n"
                    f"# License: {license_info}\n"
                    f"# Retrieved: {__import__('datetime').datetime.utcnow().isoformat()}Z\n"
                    f"# Attribution: upstream prompt collected by General Ludd\n"
                    f"# — see {repo_url}/blob/main/LICENSE for full license text\n"
                    f"#\n"
                )
                yaml.dump(profile_data, f, default_flow_style=False, allow_unicode=True)
            collected.append(out_path)
            print(f"    Wrote {out_path.name} ({len(prompt_text)} chars)")

    return collected


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect system prompts from coding agents")
    parser.add_argument(
        "--output-dir",
        default="config/prompt_profiles/collected",
        help="Output directory for collected prompts",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="Specific source(s) to collect from (default: all)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sources_to_collect = (
        {k: v for k, v in SOURCES.items() if k in args.source}
        if args.source
        else SOURCES
    )

    total = 0
    for source_name, source_info in sources_to_collect.items():
        print(f"\nCollecting from {source_name} ({source_info['repo']})...")
        files = collect_source(source_name, source_info, output_dir)
        total += len(files)

    print(f"\nCollected {total} prompts to {output_dir}/")


if __name__ == "__main__":
    main()
