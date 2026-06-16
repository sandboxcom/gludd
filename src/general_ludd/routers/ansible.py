from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

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
        # SECURITY (CRITICAL SSTI->RCE): this endpoint accepts an attacker-
        # controlled template body. It MUST NOT use the trusted full-Templar
        # path (AnsibleTemplater.render -> Templar.template), which exposes the
        # whole lookup/plugin surface — {{ lookup('pipe','id') }} would run a
        # shell. We render in a jinja2 SandboxedEnvironment with StrictUndefined
        # and an empty global namespace (no lookup/plugin/range), wrapping every
        # extra-var value Ansible-unsafe so a payload smuggled through a variable
        # value renders literally. The render fails CLOSED: a SecurityError /
        # undefined / syntax error becomes an HTTP 400 with no traceback.
        #
        # This route is intentionally NOT in the daemon's _PUBLIC_PATHS allow-
        # list, so when GLUDD_PSK is configured it requires the daemon PSK.
        from general_ludd.ansible.templating import AnsibleTemplater, TemplateRenderError

        extra_vars = req.get("extra_vars", {})
        if not isinstance(extra_vars, dict):
            raise HTTPException(status_code=400, detail="extra_vars must be an object")
        templater = AnsibleTemplater(extra_vars=extra_vars)
        try:
            rendered = templater.render_sandboxed(str(req.get("template", "")))
        except TemplateRenderError:
            # Fail closed: no traceback, no internal detail.
            raise HTTPException(
                status_code=400,
                detail="template rejected by sandbox",
            ) from None
        return {"rendered": rendered}
