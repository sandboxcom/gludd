#!/usr/bin/env python3
"""Validate Molecule inputs through installed Molecule and Ansible correctness owners."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from contextlib import chdir
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

ROOT = Path(__file__).resolve().parent.parent
LINT_BOOTSTRAP = (
    "import runpy, sys\n"
    "from ansiblelint.file_utils import Lintable\n"
    "from ansiblelint.runner import threads\n"
    "paths = sys.argv[sys.argv.index('--') + 1:]\n"
    "syntax_inputs = sum(Lintable(path).kind in {'playbook', 'role', 'pattern'} for path in paths)\n"
    "workers = threads()\n"
    "if workers < 1 or min(workers, syntax_inputs) > 8:\n"
    "    raise SystemExit(f'MOLECULE_VALIDATION_CAPACITY_FAILED workers={workers} "
    "syntax_inputs={syntax_inputs} maximum=8')\n"
    "print(f'MOLECULE_VALIDATION_CAPACITY workers={workers} syntax_inputs={syntax_inputs} "
    "active_maximum={min(workers, syntax_inputs)}', flush=True)\n"
    "runpy.run_module('ansiblelint', run_name='__main__')\n"
)


def discover_inputs(root: Path) -> tuple[Path, ...]:
    """Retain the existing scenario/task scope once, including both YAML suffixes."""
    patterns = (
        "molecule/**/*.yml", "molecule/**/*.yaml",
        "collections/**/molecule/**/*.yml", "collections/**/molecule/**/*.yaml",
        "collections/**/roles/*/tasks/*.yml", "collections/**/roles/*/tasks/*.yaml",
    )
    return tuple(sorted({path.resolve(strict=True) for pattern in patterns for path in root.glob(pattern)}))


def required_playbooks(config_file: Path) -> tuple[Path, ...]:
    """Use Molecule's migrated defaults and native resolver, without running actions."""
    from molecule.config import Config

    config = Config(str(config_file))
    actions = {entry.split()[0] for entry in config.config_data["scenario"]["test_sequence"]}
    provisioner = config.provisioner
    required: list[tuple[str, str | None]] = []
    if actions.intersection({"syntax", "converge", "idempotence", "check"}):
        if provisioner is None:
            raise ValueError(f"{config_file}: active convergence has no Ansible provisioner")
        required.append(("converge", provisioner.playbooks.converge))
    if "verify" in actions and config.config_data["verifier"]["name"] == "ansible":
        from molecule.util import safe_load_file

        original = cast(dict[str, object], safe_load_file(config_file))
        scenario = cast(dict[str, object], original.get("scenario", {}))
        explicit = "test_sequence" in scenario
        for section in ("provisioner", "ansible"):
            data = cast(dict[str, object], original.get(section, {}))
            playbooks = cast(dict[str, object], data.get("playbooks", {}))
            explicit = explicit or "verify" in playbooks
        resolved = provisioner.playbooks.verify if provisioner is not None else None
        if explicit or resolved is not None:
            required.append(("verify", resolved))
        else:
            print(f"MOLECULE_VALIDATION_VERIFY_SKIPPED config={config_file} proof=none", flush=True)
    paths: list[Path] = []
    for action, resolved in required:
        if resolved is None or not Path(resolved).is_file():
            raise ValueError(f"{config_file}: active {action} requires a real resolved playbook")
        paths.append(Path(resolved).resolve(strict=True))
    return tuple(paths)


class _PluginContext(Protocol):
    resolved: bool
    plugin_resolved_path: str | None
    plugin_resolved_collection: str | None


class _ValidationResult(Protocol):
    error_messages: list[str]


class _ArgumentValidator(Protocol):
    def validate(self, parameters: dict[str, object]) -> _ValidationResult: ...


def validate_action_option_names(paths: tuple[Path, ...]) -> None:
    """Validate documented action option names/aliases only; never template values or run code."""
    from ansible.errors import AnsibleParserError
    from ansiblelint.file_utils import Lintable
    from ansiblelint.utils import task_in_list

    loaders = importlib.import_module("ansible.plugins.loader")
    initialize = cast(Callable[..., None], loaders.init_plugin_loader)
    initialize(os.environ["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep))
    find_action = cast(Callable[..., _PluginContext], loaders.action_loader.find_plugin_with_context)
    find_module = cast(Callable[..., _PluginContext], loaders.module_loader.find_plugin_with_context)
    docs = importlib.import_module("ansible.utils.plugin_docs")
    read_docs = cast(Callable[..., tuple[object, ...]], docs.get_plugin_docs)
    arguments = importlib.import_module("ansible.module_utils.common.arg_spec")
    validator_type = cast(Callable[[dict[str, object]], _ArgumentValidator], arguments.ArgumentSpecValidator)
    diagnostics: list[str] = []
    for path in paths:
        lintable = Lintable(str(path))
        if lintable.kind not in {"playbook", "tasks", "handlers"}:
            continue
        for task in task_in_list(data=lintable.data, kind=lintable.kind, file=lintable):
            if task.error is not None:
                diagnostics.append(f"{path}: {str(task.error)[:500]}")
                continue
            action = cast(dict[str, object], task["action"])
            name = action.get("__ansible_module_original__")
            if not isinstance(name, str):
                diagnostics.append(f"{path}: native task metadata has no module identity")
                continue
            action_context = find_action(name, ignore_deprecated=True, check_aliases=True)
            if not action_context.resolved:
                continue
            module_context = find_module(name, ignore_deprecated=True, check_aliases=True)
            options: dict[str, dict[str, object]] | None = None
            for context, plugin_type, loader in (
                (module_context, "module", loaders.module_loader),
                (action_context, "action", loaders.action_loader),
            ):
                if not context.resolved:
                    continue
                try:
                    document = read_docs(name, plugin_type, loader, loaders.fragment_loader, False)[0]
                except AnsibleParserError as exc:
                    # Native absence has no cause; native parse/fragment failures retain their cause.
                    if exc.__cause__ is not None or not str(exc).startswith("No documentation available for "):
                        raise
                    continue
                if not isinstance(document, dict):
                    raise ValueError(f"{path}: invalid native documentation for {name}")
                if "options" not in document:
                    continue
                if not isinstance(document["options"], dict):
                    raise ValueError(f"{path}: invalid native options metadata for {name}")
                options = cast(dict[str, dict[str, object]], document["options"])
                break
            if options is None:
                print(
                    f"MOLECULE_VALIDATION_ACTION_OPTIONS_UNSUPPORTED path={path} line={task.line} "
                    f"action={name} reason=native-options-unavailable proof=none",
                    flush=True,
                )
                continue
            if "key_value" in options:
                continue
            specification: dict[str, object] = {}
            for option, description in options.items():
                aliases = cast(list[str], description.get("aliases", []))
                if option in {"free-form", "free_form"}:
                    aliases = [*aliases, "_raw_params"]
                specification[option] = {"type": "raw", "aliases": aliases}
            validator = validator_type(specification)
            result = validator.validate({key: value for key, value in action.items() if not key.startswith("__")})
            errors = result.error_messages
            if errors:
                diagnostics.append(f"{path}:{task.line}: {name}: {'; '.join(errors)[:500]}")
    if diagnostics:
        raise ValueError("\n".join(diagnostics))


def validate_native_lint_diagnostics(log: Path) -> bool:
    """Reject emitted native diagnostics without changing logging or native rules."""
    accepted = True
    diagnostic = re.compile(
        r"^\s*(?:\[(?:DEPRECATION )?(?:WARNING|ERROR|CRITICAL|FATAL)\]|"
        r"(?:WARNING|ERROR|CRITICAL|FATAL)\b)"
    )
    with log.open(encoding="utf-8") as output:
        for line in output:
            if diagnostic.match(line):
                accepted = False
                print(
                    f"MOLECULE_VALIDATION_DIAGNOSTIC_FAILED: {line.rstrip()} proof=none",
                    file=sys.stderr, flush=True,
                )
    return accepted


def main(*, lint_timeout_seconds: int = 45) -> int:
    """Run fail-closed, offline validation in private temporary state, never convergence."""
    original_environment = dict(os.environ)
    try:
        if type(lint_timeout_seconds) is not int or not 1 <= lint_timeout_seconds <= 120:
            raise ValueError("lint timeout must be an integer from 1 through 120")
        files = discover_inputs(ROOT)
        if not files:
            raise ValueError("no Molecule or collection task YAML inputs found")
        with tempfile.TemporaryDirectory(prefix="gludd-molecule-yaml-") as temporary:
            state = Path(temporary)
            (state / "tmp").mkdir()
            os.environ.pop("VIRTUAL_ENV", None)
            os.environ.update(
                ANSIBLE_HOME=str(state / "ansible-home"),
                ANSIBLE_LOCAL_TEMP=str(state / "tmp"),
                ANSIBLE_LINT_SKIP_SCHEMA_UPDATE="1",
                MOLECULE_PROJECT_DIRECTORY=str(ROOT),
                MOLECULE_EPHEMERAL_DIRECTORY=str(state / "molecule"),
                MOLECULE_PARALLEL="false",
                PYTHONWARNINGS="error",
                PYTHON_CPU_COUNT="8",
                TMPDIR=str(state / "tmp"),
            )
            collections = str(ROOT / "collections")
            inherited = original_environment.get("ANSIBLE_COLLECTIONS_PATH")
            os.environ["ANSIBLE_COLLECTIONS_PATH"] = collections + (os.pathsep + inherited if inherited else "")
            configs = tuple(path for path in files if path.name in {"molecule.yml", "molecule.yaml"})
            lint_inputs = set(files).difference(configs)
            print(f"MOLECULE_VALIDATION_CONFIG_START scenarios={len(configs)}", flush=True)
            config_errors: list[str] = []
            for config in configs:
                identity = hashlib.sha256(str(config).encode()).hexdigest()
                os.environ["MOLECULE_EPHEMERAL_DIRECTORY"] = str(state / "molecule" / identity)
                try:
                    lint_inputs.update(required_playbooks(config))
                except (Exception, SystemExit) as exc:
                    config_errors.append(f"{config}: {str(exc)[:500]}")
            if config_errors:
                for error in config_errors:
                    print(f"MOLECULE_VALIDATION_CONFIG_FAILED: {error}", file=sys.stderr, flush=True)
                return 1
            for path in files:
                path.read_text(encoding="utf-8")
            if not lint_inputs:
                print("MOLECULE_VALIDATION_PASS scenarios validated; no active playbook inputs", flush=True)
                return 0
            configuration = state / "ansible-lint.yml"
            configuration.write_text(
                json.dumps(
                    {"profile": "min", "enable_list": ["args", "jinja"], "warn_list": [],
                     "skip_list": [], "exclude_paths": [], "offline": True,
                     "kinds": [
                         {"tasks": "**/molecule/shared/mock_daemon_start.yml"},
                         {"tasks": "**/molecule/shared/mock_daemon_stop.yml"},
                     ]}
                ),
                encoding="utf-8",
            )
            ignore = state / "ansible-lint-ignore"
            ignore.write_text("", encoding="utf-8")
            validate_action_option_names(tuple(sorted(lint_inputs)))
            command = [
                sys.executable, "-X", "cpu_count=8", "-c", LINT_BOOTSTRAP,
                "--offline", "--nocolor", "--strict",
                "--config-file", str(configuration), "--ignore-file", str(ignore),
                "--project-dir", str(ROOT), "--",
                *map(str, sorted(lint_inputs)),
            ]
            print(f"MOLECULE_VALIDATION_LINT_START files={len(lint_inputs)} timeout={lint_timeout_seconds}", flush=True)
            repository = Path(__file__).resolve().parents[1]
            if str(repository) not in sys.path:
                sys.path.insert(0, str(repository))
            if TYPE_CHECKING:
                from stream_command import stream_command
            else:
                from scripts.stream_command import stream_command

            with chdir(state):
                log = state / "ansible-lint.log"
                returncode = stream_command(
                    command, log, observed_root=state / "observed",
                    label="gludd-molecule-yaml", max_seconds=lint_timeout_seconds, heartbeat_seconds=10,
                )
            print(f"MOLECULE_VALIDATION_LINT_END rc={returncode}", flush=True)
            return 0 if returncode == 0 and validate_native_lint_diagnostics(log) else 1
    except (Exception, SystemExit) as exc:
        print(f"MOLECULE_VALIDATION_FAILED: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        os.environ.clear()
        os.environ.update(original_environment)


def _lint_timeout(value: str) -> int:
    """Parse the bounded native-lint deadline before any validation work."""
    try:
        budget = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("lint timeout must be an integer from 1 through 120") from exc
    if not 1 <= budget <= 120:
        raise argparse.ArgumentTypeError("lint timeout must be an integer from 1 through 120")
    return budget


def cli(argv: list[str] | None = None) -> int:
    """Admit one bounded budget or print a mutation-free, non-proof plan."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lint-timeout-seconds", type=_lint_timeout, default=45, metavar="SECONDS",
        help="native lint deadline in seconds, 1 through 120 (default: 45)",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="print the admitted budget and policy without validating inputs",
    )
    arguments = parser.parse_args(argv)
    if arguments.validate_only:
        print(
            f"MOLECULE_VALIDATION_PLAN root={ROOT} "
            f"lint_timeout_seconds={arguments.lint_timeout_seconds} maximum_syntax_workers=8 "
            "offline=true profile=min enable_list=args,jinja proof=none",
            flush=True,
        )
        return 0
    return main(lint_timeout_seconds=arguments.lint_timeout_seconds)


if __name__ == "__main__":
    sys.exit(cli())
