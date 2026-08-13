# Beta.3 Molecule smoke-test reliability

The beta.3 release gate runs each Molecule scenario by name while preserving a
canonical `molecule/default/molecule.yml`. Molecule 26 probes that file even
when another scenario is selected so it can decide whether the default
scenario must manage shared resources. Without the file, a successful named
scenario begins with a misleading `CRITICAL ... glob failed` message.

Gludd's default scenario is intentionally a no-op. Individual scenarios retain
their own names and isolated lifecycle; the default configuration exists only
to satisfy Molecule's discovery and future shared-state contract. Cleanup
preserves it while deleting generated scenario directories.

## Upstream evidence

- The current
  [Molecule configuration reference](https://docs.ansible.com/projects/molecule/configuration/)
  says every role should contain a `default` scenario and documents its role
  in shared-state lifecycle management.
- The
  [Molecule workflow reference](https://docs.ansible.com/projects/molecule/workflow/)
  describes the default scenario as the shared-resource lifecycle manager.
- Users reported that Molecule 25 changed scenario/role resolution and broke
  previously working CI layouts in
  [ansible/molecule#4391](https://github.com/ansible/molecule/issues/4391).
  The thread remained open across multiple releases and contains reports of
  version pinning and path workarounds. Gludd avoids a version-specific output
  filter and supplies the canonical layout expected by current Molecule.

## Release verification

`make molecule-test SCENARIO=binary_smoke_macos` must complete syntax,
prepare, converge, and verify without `CRITICAL`, `ERROR`, or `WARNING`
messages. The scenario builds or accepts a real frozen binary, exercises
version/help/project-path commands, starts an isolated daemon, probes health,
checks stderr for packaging regressions, and removes the daemon afterward.
