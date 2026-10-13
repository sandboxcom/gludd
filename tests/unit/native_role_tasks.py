"""Render role tasks through the installed Ansible include/default owners."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from ansible.executor import module_common
from ansible.parsing.dataloader import DataLoader
from ansible.playbook.block import Block
from ansible.playbook.helpers import load_list_of_blocks
from ansible.playbook.play import Play
from ansible.playbook.task import Task
from ansible.playbook.task_include import TaskInclude
from ansible.template import Templar, trust_as_template
from ansible.vars.manager import VariableManager


def role_tasks(
    role: Path, variables: dict[str, Any], *, entrypoint: str = "main.yml", expand_includes: bool = True,
) -> Iterator[tuple[Task, Templar]]:
    """Resolve native conditional includes and return each effective task context."""
    collection = importlib.import_module("ansible_collections.general_ludd.agent")
    if not hasattr(collection, "_collection_meta"):
        # Other module tests may import the package before installing Ansible's
        # finder. Reload through that finder so native defaults see real metadata.
        importlib.reload(collection)
    loader = DataLoader()
    loader.set_basedir(str(role / "tasks"))
    def source(path: Path) -> Any:
        return loader.load(trust_as_template(path.read_text(encoding="utf-8")), file_name=str(path))

    defaults = source(role / "defaults/main.yml")
    context = {**defaults, **variables}
    variable_manager = VariableManager(loader=loader)
    play = Play.load(
        {"hosts": "localhost", "gather_facts": False,
         "vars": context,
         "tasks": source(role / "tasks" / entrypoint)},
        loader=loader, variable_manager=variable_manager,
    )

    def visit(items: list[Any], directory: Path) -> Iterator[tuple[Task, Templar]]:
        for task in items:
            if isinstance(task, Block):
                yield from visit(task.block, directory)
                continue
            templar = Templar(loader=loader, variables={**context, **task.get_vars(), "omit": "__native_role_omit__"})
            if not all(templar._engine.evaluate_conditional(condition) for condition in task.when):
                continue
            if isinstance(task, TaskInclude):
                if not expand_includes:
                    yield task, templar
                    continue
                file = directory / templar.template(task.args["_raw_params"])
                loader.set_basedir(str(file.parent))
                blocks = load_list_of_blocks(
                    source(file), play=play,
                    parent_block=task.build_parent_block(), loader=loader,
                    variable_manager=variable_manager,
                )
                yield from visit(blocks, file.parent)
                loader.set_basedir(str(directory))
            else:
                if task.action == "ansible.builtin.set_fact":
                    internal = {templar.template(key): value for key, value in task.args.items()
                                if str(templar.template(key)).startswith("_")}
                    context.update(templar.template(internal))
                yield task, templar

    yield from visit(play.compile(), role / "tasks")


def effective_arguments(task: Task, templar: Templar) -> dict[str, Any]:
    """Apply native inherited defaults before native argument rendering."""
    if hasattr(module_common, "_apply_action_arg_defaults"):
        merged = module_common._apply_action_arg_defaults(task.action, task, task.args, templar)
    else:
        merged = module_common.get_action_args_with_defaults(
            task.action, task.args, task.module_defaults, templar,
            action_groups=task.get_play()._action_groups,
        )
    rendered = templar.template(merged)
    return {key: value for key, value in cast(dict[str, Any], rendered).items() if value != "__native_role_omit__"}

