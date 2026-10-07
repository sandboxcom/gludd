# Hosted Molecule Build and Runtime Isolation

Status: locally verified for the v0.1.1 replacement candidate; exact-SHA hosted
proof remains required.

## Failure and isolated build owner

Molecule run `37684090064` exposed two shared-environment assumptions. Shard 1
prepared the complete CI/Molecule environment in `.venv`, then the native Linux
binary path synchronized the narrower `build-azure` profile into that same
directory. The exact sync correctly removed packages outside the build profile,
including Molecule itself, so a successful artifact build could not continue to
the scenario runner. `build-executable` now owns a worktree-namespaced
`pyinstaller-build-env` beneath the resource-arbiter root. Every native
interpreter, analyzer, and warning-audit invocation selects that environment
explicitly and uses `--no-sync`; the CI/Molecule `.venv` remains unchanged.

This boundary reflects sustained practitioner reports rather than a
Gludd-specific workaround. uv issue
[#9261](https://github.com/astral-sh/uv/issues/9261) demonstrates a selected sync
removing packages from a different dependency group, and issue
[#13749](https://github.com/astral-sh/uv/issues/13749) documents shared-workspace
environment churn that ends in `ModuleNotFoundError`. The isolation treats that
exact synchronization as intended ownership behavior and gives the build its
own exact environment instead of making sync inexact or reinstalling the test
runner afterward.

## Reviewed warning graph

The retained `molecule-linux-pyinstaller-warning-shard-1` artifacts from runs
`37684090064` and `37692342280` each contained 861 edges, but the latter
changed from the reviewed digest
`250f40043cbf269ea17ec2d6e1bf6fbcd598ff41405b0575e636c61f091fef60`
to `b2e25c4ef97344329f17b946ba551a7554c07e4ffbc89580360dd40ce6455cb7`.
The raw artifact comparison found exactly one addition and one removal: the
importer for `pyimod02_importers` moved from the runner's `.venv` to the
worktree-namespaced `pyinstaller-build-env`, while both paths ended in the same
`PyInstaller/hooks/rthooks/pyi_rth_pkgutil.py` file. No module, flags, or graph
topology changed.

The audit now canonicalizes PyInstaller hook importers from the GitHub runner
`.venv` and worktree-namespaced `pyinstaller-build-env` to the package-relative
hook path. Other build roots remain exact, preserving the independently
reviewed container and aarch64 identities. Both raw artifacts therefore
produce the reviewed 861-edge digest
`a833e5857ff994bf49d2f83d620d526faadd974306ebc79dc7adae8f02f9e9b6`.
The receipt binds the prior and hosted raw artifact hashes and records the exact
one-edge path canonicalization; no hash-only alternate or missing-import
allowlist was added.

This is consistent with long-lived user reports. PyInstaller issue
[#1668](https://github.com/pyinstaller/pyinstaller/issues/1668) has tracked
environment-sensitive reproducibility since 2015. A later Windows report,
issue [#8890](https://github.com/pyinstaller/pyinstaller/issues/8890), shows
`pyi_rth_pkgutil.py` being loaded from an absolute interpreter-specific
`site-packages` path. Those reports support removing only the installation root
while retaining the PyInstaller package and hook identity in the reviewed
graph.

## Explicit runtime validation boundary

Shard 5 showed the other side of explicit isolation. `runtime_validate` relied
on ambient authentication and `PWD`, although Molecule executed the playbook
from the scenario directory. Both converge and verify now resolve source and
`playbooks/noop.yml` through `MOLECULE_PROJECT_DIRECTORY` and pass a
scenario-only PSK to the validation subprocess. They deliberately do not set
`GLUDD_PSK_DISABLE` or `GLUDD_ALLOW_NO_AUTH`, so production remains fail-closed
when no credential is configured. Molecule issue
[#2164](https://github.com/ansible/molecule/issues/2164) preserves long-running
debug evidence that the project and scenario directories are distinct exported
contracts, while Molecule's
[CI guidance](https://github.com/ansible/molecule/blob/main/docs/ci.md) uses
`MOLECULE_PROJECT_DIRECTORY` for runner-independent project resolution.

## Zero downtime and rollback

The repair changes only an isolated artifact environment and a local validation
subprocess, never a serving daemon or release ref. A failed build leaves the CI
test environment runnable, and a failed runtime check still tears down through
Molecule. Rollback reverts the cohesive repair and selects the preceding
reviewed x86_64 digest; no production authentication relaxation, data migration,
or service restart is involved.
