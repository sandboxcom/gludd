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

The retained shard-1 artifact
`molecule-linux-pyinstaller-warning-shard-1` produced the x86_64 graph
`250f40043cbf269ea17ec2d6e1bf6fbcd598ff41405b0575e636c61f091fef60`
with 861 normalized edges. It was compared with the previously accepted
1,317-edge artifact from run `37549387041`; the deterministic receipt records
all 101 additions and 557 removals. The additions are dependency-owned optional
surfaces from the locked HTTPX/OpenAI, Hugging Face, JSON Schema, LangGraph,
NumPy, and decision-codification graph. Most removals are the test/science-only
Hypothesis and NumPy pseudo-module surface that contaminated the former shared
builder, plus superseded compatibility imports. The production fail-closed
audit accepted every candidate edge and found no unreviewed project import.
The receipt binds both raw artifact hashes and retains the former digest as a
reviewed rollback identity; no hash-only exception or missing-import allowlist
was added.

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
