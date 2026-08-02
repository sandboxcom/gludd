"""Ansible module: export a chat session to markdown/json/html.

Wraps ``general_ludd.chat.session.export_session``.
"""

from __future__ import annotations

from pathlib import Path

from ansible.module_utils.basic import AnsibleModule


def run_module() -> None:
    module_args = {
        "session_file": {"type": "str", "required": True},
        "format": {"type": "str", "default": "md", "choices": ["md", "json", "html"]},
        "output_file": {"type": "str", "required": False, "default": None},
    }
    module = AnsibleModule(argument_spec=module_args)

    session_file = module.params["session_file"]
    export_format = module.params["format"]
    output_file = module.params["output_file"]

    try:
        from general_ludd.chat.session import export_session

        result = export_session(
            Path(session_file),
            format=export_format,
            output_file=Path(output_file) if output_file else None,
        )
        module.exit_json(
            changed=True,
            output=str(result) if output_file else result,
        )
    except Exception as exc:
        module.fail_json(msg=str(exc))


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
