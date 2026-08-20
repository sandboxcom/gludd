"""Ansible module: export a chat session to markdown, JSON, or HTML."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.chat.plugins.module_utils.session_export import (
    ExportFormat,
    publish_export,
    render_session,
)


def run_module() -> None:
    module_args = {
        "session_file": {"type": "str", "required": True},
        "format": {"type": "str", "default": "md", "choices": ["md", "json", "html"]},
        "output_file": {"type": "str", "required": False, "default": None},
    }
    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    session_file = module.params["session_file"]
    export_format = module.params["format"]
    output_file = module.params["output_file"]

    try:
        rendered = render_session(
            Path(session_file),
            cast(ExportFormat, export_format),
        )
        changed = False
        result: str
        if output_file:
            out_path = Path(output_file)
            previous = (
                out_path.read_text(encoding="utf-8") if out_path.is_file() else None
            )
            changed = previous != rendered
            if not module.check_mode:
                publish_export(out_path, rendered)
            result = str(out_path)
        else:
            result = rendered

        module.exit_json(
            changed=changed,
            output=result,
        )
    except Exception as exc:
        module.fail_json(msg=str(exc))


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
