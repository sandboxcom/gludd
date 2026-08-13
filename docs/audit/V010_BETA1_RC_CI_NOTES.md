# v0.1.0-beta.1 RC CI Notes

Date: 2026-07-20

## Long-Lived External Issues

- Starlette TestClient migrated to `httpx2`; plain `httpx` is deprecated for
  this path. Local mitigation: add `httpx2>=2.0.0` and regenerate `uv.lock`.
  References: https://www.starlette.io/testclient/,
  https://www.starlette.io/release-notes/. User discussion evidence:
  https://github.com/encode/starlette/discussions/2594. Revalidated for beta.3:
  `tests/e2e/test_secrets_security_workflows.py` passes without the prior
   `StarletteDeprecationWarning` after the dependency is installed.
- Starlette's TestClient `timeout=` argument has never enforced a request
  deadline; users reported the misleading API in 2020 and current Starlette
  deprecates it. Worker tests therefore use `pytest-timeout` for the test-level
  execution ceiling and a bounded server `asyncio.wait_for` for cancellation,
  while promoting `StarletteDeprecationWarning` to an error. Reference:
  https://github.com/Kludex/starlette/issues/1108
- `pkg_resources` deprecation/removal is a long-lived setuptools transition.
  The local warning came from third-party `fs`, not direct project imports.
  The beta.3 remediation replaces that local-filesystem-only dependency with
  `pathlib`/`shutil`, removes the warning filters, and permits current
  setuptools releases instead of pinning a vulnerable compatibility version.
  References: https://setuptools.pypa.io/en/stable/deprecated/pkg_resources.html,
  https://github.com/pypa/setuptools/issues/5174
- Ansible's empty-inventory warning is a recurring user issue dating back
  years. Local mitigation: molecule scenarios that target localhost should
  declare explicit localhost inventory rather than relying on implicit
  localhost behavior.
  References: https://forum.ansible.com/t/warning-provided-hosts-list-is-empty-only-localhost-is-available/20953,
  https://docs.ansible.com/projects/molecule/configuration/

## Local Fixes Validated

- `role_ai_parallel_dispatch` no longer burns the full barrier retry window per
  async job. The barrier now polls the selected wait-set directly, and the
  scenario has explicit inventory plus destroy hooks.
- `openbao_break_glass_backup`, the scenario shown failing in CI shard 1, passed
  locally before the shard was intentionally stopped to prevent the next
  scenario from spawning many secret-scan subprocesses.
- Python unit clusters from the failed CI log were rerun after fixes; targeted
  files now pass without pytest warning summaries in the rechecked endpoint/MCP
  clusters.
