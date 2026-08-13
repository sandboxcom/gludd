# Warning-Free Ansible Syntax Validation

## Contract

The tracked `make ansible-syntax` target checks every registered
`playbooks/*.yml` file with the locked Ansible runtime and an explicit inline
inventory:

```text
localhost,
```

The trailing comma selects Ansible's host-list inventory plugin. It makes the
single validation host part of `all`, so playbooks targeting either
`localhost` or `all` have a concrete inventory during parsing. Syntax
validation remains read-only: it does not execute tasks, contact an external
host, or load a deployment inventory.

The guardrail test requires both a zero exit status and warning-free output.
Warnings are treated as defects because repeating them for every playbook can
hide a new compatibility or parsing warning in CI.

## Practitioner evidence

Ansible users have reported the empty-inventory warning for at least a decade.
In the [2016 forum thread about the empty hosts
list](https://forum.ansible.com/t/warning-provided-hosts-list-is-empty-only-localhost-is-available/20953),
the command could list the intended inventory but omitted `-i` during the
actual invocation. A later [2018 inventory parsing
thread](https://forum.ansible.com/t/unable-to-parse-etc-ansible-hosts-as-an-inventory-source/27844)
shows the paired "No inventory was parsed" and implicit-localhost warnings that
the Gludd target reproduced.

The durable lesson is to declare the validation inventory at the command
boundary. Suppressing warnings would conceal a missing input and would not
exercise the same host-pattern resolution as an explicit inventory.

## Zero-downtime adoption

This target is a pre-deployment read-only check. Adding the explicit inventory
does not modify playbooks, running services, persistent state, or production
inventory selection. Old and new CI workers may overlap safely while the
change rolls out; each invocation is self-contained and validates the same
tracked playbook set. A failure blocks promotion before any deployment action.
