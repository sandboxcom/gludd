# Skill Lens Role

Surgical skill section extractor for agent prompts.

## Usage

```yaml
- name: Get Python expert advice for asyncio debugging
  ansible.builtin.include_role:
    name: general_ludd.agent.skill_lens
  vars:
    skill_name: python-expert
    task_description: "debug an asyncio deadlock in the event loop"
    max_sections: 3

- name: Use the lens output in a prompt
  ansible.builtin.set_fact:
    agent_prompt: "{{ skill_lens_prompt }}\n\n---\n\nNow fix the bug."
```

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `skill_name` | (required) | Skill identifier (e.g. `python-expert`) |
| `task_description` | `""` | What the agent is working on |
| `max_sections` | `3` | Max sections to return |
| `cache_enabled` | `true` | Use in-memory cache for repeated calls |

## Output Facts

| Fact | Description |
|------|-------------|
| `skill_lens_prompt` | Full markdown prompt from lens output |
| `skill_lens_header` | Skill title |
| `skill_lens_num_sections` | Number of sections returned |

## Python API

```python
from general_ludd.ansible.skill_lens import lens

prompt = lens("python-expert", "debug an asyncio deadlock", max_sections=3)
print(prompt)
```
