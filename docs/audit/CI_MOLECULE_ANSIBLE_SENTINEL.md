# Molecule Ansible Serializer Compatibility

The GHA run `30194175951` (SHA `2d2719a8`) failed in the
`role_debug_failure`, `role_dependency_update`, `role_document_change`, and
`role_implement_change` scenarios. All failures occurred when
`gludd_agent_run` returned its result: ansible-core 2.19's serializer imports
`ansible.module_utils.common.sentinel` dynamically, but the module payload
builder did not include that module.

The collection now explicitly imports `Sentinel` in `gludd_agent_run.py` so the
payload builder sees and bundles the dependency. The import is optional for
older controller versions, preserving compatibility while making the 2.19
path deterministic.

This is a known long-lived Ansible upgrade failure mode. The Ansible community
forum documents the same `ModuleNotFoundError` after upgrading to 2.19:
<https://forum.ansible.com/t/problem-upgrading-to-ansible-2-19/45144>.
Ansible's module-utility guidance also recommends explicit module-utils
imports and declaring the supported ansible-core range in `meta/runtime.yml`:
<https://docs.ansible.com/projects/ansible-core/devel/dev_guide/developing_collections_shared.html>.

Regression coverage is in
`tests/unit/test_gludd_agent_run_behavioral.py::test_agent_module_explicitly_bundles_ansible_sentinel_dependency`.
