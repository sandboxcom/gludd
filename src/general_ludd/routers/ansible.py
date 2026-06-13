from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from general_ludd.ansible.galaxy import get_builtin_modules, install_galaxy, search_galaxy


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.get("/admin/ansible/search")
    async def admin_ansible_search(query: str = "", type: str = "role") -> dict[str, Any]:
        results = search_galaxy(query, type)
        return {"query": query, "type": type, "results": results}

    @app.post("/admin/ansible/install")
    async def admin_ansible_install(req: dict[str, Any]) -> dict[str, Any]:
        result = install_galaxy(req.get("name", ""), req.get("type", "role"))
        return result

    @app.get("/admin/ansible/builtins")
    async def admin_ansible_builtins() -> dict[str, Any]:
        return {"modules": get_builtin_modules()}

    @app.post("/admin/ansible/render")
    async def admin_ansible_render(req: dict[str, Any]) -> dict[str, Any]:
        # Render a Jinja2 template through Ansible's variable resolution
        # (AnsibleTemplater wraps CoreAnsibleRunner) so prompts/skills can use
        # the same filter/var semantics as playbooks.
        from general_ludd.ansible.templating import AnsibleTemplater

        templater = AnsibleTemplater(extra_vars=req.get("extra_vars", {}))
        rendered = templater.render(str(req.get("template", "")))
        return {"rendered": rendered}
