"""Ansible Galaxy search/install and builtin module listing."""

from __future__ import annotations

import re
import subprocess
from typing import Any

# ---------------------------------------------------------------------------
# Argument-injection hardening (#70)
#
# ansible-galaxy is invoked with collection/role names + versions that
# originate from caller / config input. Even though we always pass a list
# argv (never shell=True), an unvalidated value that begins with "-" is
# interpreted by ansible-galaxy as an OPTION (e.g. "-r /etc/passwd" reads an
# attacker-controlled requirements file). Whitespace and shell metacharacters
# have no business in a galaxy name/version either, so we reject them defensively.
#
# Validation rules:
#   - name : ansible namespace.name form -> two or more dot-separated segments,
#            each starting with a lowercase letter, then [a-z0-9_]. This also
#            forbids leading '-', whitespace and every shell metacharacter.
#   - version (optional, after a single ':') : digits/dots plus the usual
#            semver pre-release / build tail; never starts with '-'.
#   - galaxy_type : strictly "role" or "collection".
#   - a literal "--" separates options from positional args in every argv so a
#     value can never be re-interpreted as an option even if rules ever loosen.
# ---------------------------------------------------------------------------

_NAME_SEGMENT = r"[a-z][a-z0-9_]*"
_NAME_RE = re.compile(rf"^{_NAME_SEGMENT}(?:\.{_NAME_SEGMENT})+$")
# version: must start with a digit; digits/dots and an optional semver tail.
_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")
_ALLOWED_GALAXY_TYPES = ("role", "collection")
# A safe free-form search token: no whitespace, no leading dash, no shell metachars.
_SEARCH_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_galaxy_type(galaxy_type: str) -> str:
    if galaxy_type not in _ALLOWED_GALAXY_TYPES:
        raise ValueError(
            f"invalid galaxy_type {galaxy_type!r}; expected one of {_ALLOWED_GALAXY_TYPES}"
        )
    return galaxy_type


def _validate_name_spec(name: str) -> str:
    """Validate a ``namespace.name`` or ``namespace.name:version`` spec.

    Raises ValueError on anything that could be option-injection, a shell
    metacharacter, whitespace, or a structurally invalid galaxy name/version.
    Returns the original (now-trusted) spec on success.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("galaxy name must be a non-empty string")
    if name[:1] == "-":
        raise ValueError(f"galaxy name may not begin with '-' (option injection): {name!r}")

    # Split off an optional version on the FIRST ':' only.
    base, sep, version = name.partition(":")
    if not _NAME_RE.match(base):
        raise ValueError(
            f"invalid galaxy name {base!r}; expected namespace.name "
            "([a-z][a-z0-9_]* segments separated by dots)"
        )
    if sep and not _VERSION_RE.match(version):
        raise ValueError(f"invalid galaxy version {version!r}")
    return name


def _validate_search_query(query: str) -> str:
    if not isinstance(query, str) or not query:
        raise ValueError("galaxy search query must be a non-empty string")
    if query[:1] == "-":
        raise ValueError(f"search query may not begin with '-' (option injection): {query!r}")
    if not _SEARCH_TOKEN_RE.match(query):
        raise ValueError(
            f"invalid search query {query!r}; only [A-Za-z0-9._-] allowed, no whitespace/metachars"
        )
    return query


BUILTIN_MODULES = [
    "ansible.builtin.copy",
    "ansible.builtin.file",
    "ansible.builtin.template",
    "ansible.builtin.command",
    "ansible.builtin.shell",
    "ansible.builtin.service",
    "ansible.builtin.package",
    "ansible.builtin.user",
    "ansible.builtin.group",
    "ansible.builtin.git",
    "ansible.builtin.uri",
    "ansible.builtin.get_url",
    "ansible.builtin.debug",
    "ansible.builtin.assert",
    "ansible.builtin.set_fact",
    "ansible.builtin.include_role",
    "ansible.builtin.import_role",
    "ansible.builtin.cron",
    "ansible.builtin.systemd",
    "ansible.builtin.lineinfile",
    "ansible.builtin.blockinfile",
    "ansible.builtin.stat",
    "ansible.builtin.find",
    "ansible.builtin.replace",
    "ansible.builtin.wait_for",
    "ansible.builtin.pause",
    "ansible.builtin.fail",
    "ansible.builtin.fetch",
    "ansible.builtin.unarchive",
    "ansible.builtin.archive",
    "ansible.builtin.pip",
    "ansible.builtin.synchronize",
    "ansible.builtin.script",
    "ansible.builtin.raw",
    "ansible.builtin.meta",
    "ansible.builtin.add_host",
    "ansible.builtin.include_vars",
    "ansible.builtin.include_tasks",
    "ansible.builtin.import_tasks",
    "ansible.builtin.import_playbook",
]


def get_builtin_modules() -> list[str]:
    return sorted(BUILTIN_MODULES)


def parse_galaxy_search_output(output: str) -> list[dict[str, Any]]:
    if not output.strip():
        return []
    results: list[dict[str, Any]] = []
    for line in output.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("Found") or stripped.startswith("----"):
            continue
        if stripped.startswith("Name") and "Description" in stripped:
            continue
        match = re.match(r"^(\S+)\s+(.*)", stripped)
        if match:
            results.append({"name": match.group(1), "description": match.group(2).strip()})
    return results


def search_galaxy(query: str, galaxy_type: str = "role") -> list[dict[str, Any]]:
    galaxy_type = _validate_galaxy_type(galaxy_type)
    query = _validate_search_query(query)
    # "--" terminates option parsing so the (validated) query can never be
    # re-read as an option.
    cmd = ["ansible-galaxy", galaxy_type, "search", "--platforms", "all", "--", query]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return parse_galaxy_search_output(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return [{"name": "error", "description": str(exc)}]


def install_galaxy(name: str, galaxy_type: str = "role") -> dict[str, Any]:
    galaxy_type = _validate_galaxy_type(galaxy_type)
    name = _validate_name_spec(name)
    # "--" terminates option parsing so the (validated) name spec can never be
    # re-read as an option (e.g. "-r requirements.yml").
    cmd = ["ansible-galaxy", galaxy_type, "install", "--", name]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        success = result.returncode == 0
        return {
            "name": name,
            "type": galaxy_type,
            "success": success,
            "output": result.stdout[:500] if success else result.stderr[:500],
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"name": name, "type": galaxy_type, "success": False, "output": str(exc)}
