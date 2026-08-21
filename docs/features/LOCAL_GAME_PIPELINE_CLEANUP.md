# Local Game Pipeline Cleanup and Image Dependency Boundary

## Contract

The local game-generation role writes the inference server PID beneath its
namespaced artifact directory. Its terminal cleanup phase tolerates a missing
PID file, sends `SIGTERM` only when a PID was actually read, and always removes
the PID file. A failed generation or verification must not turn cleanup into a
second failure or leave stale process metadata behind.

The `game-e2e` and `e2e-all` extras explicitly require `pillow>=12.3.0`. The
project lock retains exact artifacts and hashes, so game-image processing does
not rely on a weaker transitive dependency floor.

## Mature behavior and compatibility

Ansible documents that an [`always` section runs regardless of block or rescue
results](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_blocks.html).
The role therefore keeps PID discovery, termination, and idempotent file removal
in the terminal `always` list. Missing-file tolerance is local to the slurp task;
other generation and validation errors still fail normally.

Pillow's maintained [release policy](https://pillow.readthedocs.io/en/stable/releasenotes/)
states that functionality and security fixes should not be expected to be
backported. Version 12.3.0 includes decompression-bomb limits, memory-safety
repairs, and command-injection hardening, so both image-producing extras declare
that floor directly.

## Security and resources

- The PID comes only from the role-owned artifact path and is passed to
  `ansible.builtin.command`, not a shell. No public input expands the kill scope.
- Cleanup is bounded to one PID read, at most one `SIGTERM`, and one idempotent
  file removal. It adds no retry loop, daemon, or persistent worker.
- Pillow remains locked with artifact hashes. The application still restricts
  accepted reference inputs and does not treat a dependency pin as a substitute
  for image-size, format, CPU, or memory limits.
- Motion correlation validates every truncated signature with `numpy.isfinite`
  before covariance arithmetic. A NaN or infinity returns the neutral `0.0`
  score without entering NumPy's subtract/divide path or masking global
  floating-point warnings.

The long-running Pillow
[decompression-bomb practitioner report](https://github.com/python-pillow/Pillow/issues/515)
shows why compressed input size alone cannot bound decoded memory. That report
has shaped Pillow's default protections for more than a decade; Gludd keeps the
current security floor while retaining its own resource preflight.

On 2013-04-02, NumPy users documented
[`invalid value encountered in subtract`](https://github.com/numpy/numpy/issues/3190)
becoming an exception when warnings were promoted, demonstrating why invalid
numeric input must be handled before arithmetic rather than hidden with a
warning filter. NumPy's maintained
[`isfinite` contract](https://numpy.org/doc/stable/reference/generated/numpy.isfinite.html)
explicitly classifies NaN and both infinities as non-finite, while
[`corrcoef`](https://numpy.org/doc/stable/reference/generated/numpy.corrcoef.html)
defines finite correlation coefficients in the inclusive `[-1, 1]` interval.
These upstream references were reviewed on 2026-08-20.

## ZDD and rollback

The dependency pin changes installation resolution before runtime traffic. The
cleanup structure is backward compatible with existing artifact directories and
also succeeds when no server was started, so rolling workers may use old or new
role content during promotion. Promote the locked environment first, exercise a
network-free game-reference preflight, then roll the role.

Rollback restores the previous role and lock together. Operators should first
confirm that no namespaced game server remains; stale PID files may be removed
only through the same ownership-confined cleanup path. A Pillow downgrade must
not be used as rollback while the older version lacks required security fixes.

The motion-input check is stateless and wire-format neutral. Mixed old and new
workers can overlap during a zero-downtime rollout; the only behavior change is
that non-finite signatures deterministically produce `0.0` without a warning.
Rollback requires no drain, schema action, cache purge, or artifact cleanup.

## Observability and verification

The role emits explicit cleanup-started and cleanup-completed messages. Missing
PID files remain visible in task output without failing the play, while attempted
termination remains a distinct task result. Focused tests pin missing-file
tolerance, terminal kill and removal, the direct Pillow floor in both extras,
and the network-free reference-preflight contract.
Motion tests pin NaN and infinity to the neutral score under warnings-as-errors.
