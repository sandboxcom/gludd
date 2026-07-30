# Molecule warning contract

Gludd's localhost scenarios are component tests, not infrastructure-provisioning
scenarios. Each such scenario must therefore declare an explicit `localhost`
inventory and list only actions backed by a real playbook. A scenario must not
inherit Molecule's full default sequence when it has no dependency, lifecycle,
or side-effect implementation.

The upstream workflow reference defines the full default sequence and also
documents smaller component sequences. Its configuration reference says the
delegated/default driver makes the project responsible for `create` and
`destroy`, while Docker and Podman drivers provide those playbooks. It also
documents `requirements.yml` and `collections.yml` as the Galaxy defaults:

- [Molecule workflow reference](https://docs.ansible.com/projects/molecule/workflow/)
- [Molecule configuration reference](https://docs.ansible.com/projects/molecule/configuration/)

Long-lived user discussions show the practical source of the ambiguity:
localhost-focused tests are often modeled as container scenarios even when no
container lifecycle exists, and users must override the default sequence to
match the test they actually implement:

- [How to simulate local host in Molecule](https://www.reddit.com/r/ansible/comments/1jgtt0p/)
- [Seeking help with Molecule testing for a DNS stack](https://www.reddit.com/r/ansible/comments/1dmpxi3/)

The enforced repository contract is:

1. Empty `platforms` requires an explicit `localhost` inventory with
   `ansible_connection: local`.
2. Every configured provisioner action resolves to a real playbook. Driver
   supplied `create` and `destroy` actions are allowed for non-default drivers.
3. `dependency` is present only when both Galaxy manifest files exist.
4. Ansible modules emit warnings through `AnsibleModule.warn()` and never pass
   reserved `warnings` or `deprecations` keys to `exit_json`/`fail_json`.
5. Role defaults use role-prefixed names instead of Ansible-reserved variables
   such as `timeout`.
6. Rendered list text starts with a stable label, not a bare numeric Jinja
   expression. Python 3.11 can otherwise emit `invalid decimal literal` while
   Ansible attempts native literal conversion of text such as `2 failures`.

`tests/unit/test_molecule_warning_contract.py` ratchets the release inventory at
123 logical scenarios and checks both canonical `molecule/playbooks/*` sources
and any tracked `molecule/*` runtime copies with the same scenario name. It also
walks every scenario playbook conditional, so stale runtime configurations and
deprecated Jinja-delimited conditions fail before hosted CI. The same guard
checks role defaults and numeric-leading rendered list strings.
