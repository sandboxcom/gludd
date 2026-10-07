# Prompt-evaluation project-root resolution

## Contract

The `prompt_eval` Molecule scenario resolves `templates/prompts` from
`MOLECULE_PROJECT_DIRECTORY`. It must not infer the repository from `PWD`,
because Molecule and hosted runners may execute a scenario from another working
directory. Hosted run `37692342280` exposed that failure by searching the
scenario directory and discovering no prompt templates.

The regression test parses both converge and verify playbooks and requires the
same explicit root. The scenario test then compiles and renders every prompt
template from that root, including its idempotence pass. This preserves
zero-downtime development: the change only affects test-time path discovery and
does not start, stop, or reconfigure an application process.

## Rollback

Revert the two playbook variable changes and their paired regression test and
documentation in one commit. No data migration or runtime cleanup is required.
Reintroducing `PWD` alone is unsafe because it restores runner-dependent
template discovery.

## Practitioner evidence

Molecule users have reported directory-layout assumptions breaking otherwise
valid scenarios since 2018. The maintained Molecule CI guide now recommends
resolving the role root with `MOLECULE_PROJECT_DIRECTORY` for multibranch CI,
which is the same stable boundary used here. Reviewed 2026-10-07:

- [Molecule issue #1567: base directory naming](https://github.com/ansible/molecule/issues/1567)
- [Molecule continuous-integration guide](https://github.com/ansible/molecule/blob/main/docs/ci.md#jenkins-pipeline)

