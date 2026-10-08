# Local Game Pipeline Cleanup and Image Dependency Boundary

## Contract

The local game-generation role delegates inference ownership to the
authenticated Gludd daemon. When that daemon selects its Ansible adapter, the
local-model stop playbook accepts the manager-provided PID or inspects the
server-ID-namespaced PID file. Its terminal cleanup tolerates a missing file,
signals only a positive numeric PID, and always removes the PID file. A failed
generation or verification must not turn cleanup into a second failure or
leave stale process metadata behind.

The isolated `game-e2e` dependency profile explicitly requires
`pillow>=12.3.0`. Its lock retains exact artifacts and hashes, so game-image
processing does not rely on a weaker transitive dependency floor. Both the
`game-e2e` and `e2e-all` installation sets include that one locked profile.

The authoritative artifacts are deliberately narrow:

- `playbooks/local_model_stop.yml` owns terminal process and PID-file cleanup.
- `requirements/profiles/game-e2e/pyproject.toml` declares the direct Pillow
  floor, while `requirements/profiles/game-e2e/uv.lock` pins its artifacts.
- `tests/unit/test_local_game_cleanup_contract.py` binds the missing-PID,
  terminal-cleanup, fail-closed generation, profile, and lock contracts.

## Mature behavior and compatibility

Ansible documents that an [`always` section runs regardless of block or rescue
results](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_blocks.html).
The playbook therefore resolves an optional PID in its main block and keeps
termination plus idempotent file removal in terminal `always` paths. A `stat`
guard avoids reading an absent file, while `failed_when: false` closes the
remaining inspect/read race. Other generation and validation errors still fail
normally.

Ansible users reported inconsistent historical behavior when
[`state: absent` encountered a missing path](https://github.com/ansible/ansible/issues/44318),
despite the documented idempotent contract. That practitioner report was
reviewed on 2026-10-07. Gludd keeps both the explicit missing-file regression
and the modern idempotent file module rather than relying on an assumption that
cleanup was exercised only with an existing PID file.

Pillow's maintained [release policy](https://pillow.readthedocs.io/en/stable/releasenotes/)
states that functionality and security fixes should not be expected to be
backported. Version 12.3.0 includes decompression-bomb limits, memory-safety
repairs, and command-injection hardening, so the image-producing profile declares
that floor directly.

## Security and resources

- The PID comes only from daemon state or its server-ID-namespaced path, must
  match `^[1-9][0-9]*$`, and is passed through `ansible.builtin.command.argv`,
  not a shell. No public input expands the kill scope.
- Cleanup is bounded to one PID read, at most one `SIGTERM`, ten one-second
  process probes, an optional `SIGKILL`, and one idempotent file removal. It
  adds no daemon or persistent worker.
- Pillow remains locked with artifact hashes. The application still restricts
  accepted reference inputs and does not treat a dependency pin as a substitute
  for image-size, format, CPU, or memory limits.
- Motion correlation validates every truncated signature with `numpy.isfinite`
  before covariance arithmetic. A NaN or infinity returns the neutral `0.0`
  score without entering NumPy's subtract/divide path or masking global
  floating-point warnings.
- SSIM uses the stable global formula for spatial extents below three pixels and
  an odd local window at larger extents. The default fidelity E2E generates and
  closes a deterministic local clip; YouTube acquisition runs only when
  `GAME_E2E_REFERENCE_NETWORK=1` is explicitly set.

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

CPython's maintained
[`Popen` context-manager contract](https://github.com/python/cpython/blob/main/Doc/library/subprocess.rst)
closes standard file descriptors and waits for the child on exit. A practitioner
report opened on 2024-01-18 documents how delayed finalization can otherwise
[retain subprocess pipes and orphan a child](https://github.com/python/cpython/issues/114177).
These references were reviewed on 2026-08-20. Gludd therefore releases a
completed game child from its ownership list and closes its parent-side stdout
and stderr pipes immediately after wait; finalization is only an idempotent
fallback for a genuinely live owned child.

Scikit-image's long-lived
[`structural_similarity` small-image report](https://github.com/scikit-image/scikit-image/issues/5366)
documents that local SSIM windows are undefined for undersized images. Yt-dlp's
maintained [known-issues thread](https://github.com/yt-dlp/yt-dlp/issues/3766)
documents YouTube availability, authentication, throttling, and HTTP failures
outside the caller's control. These sources were reviewed on 2026-08-20 and
support a mathematical tiny-frame fallback plus hermetic CI media.

## ZDD and rollback

The dependency pin changes installation resolution before runtime traffic. The
cleanup structure is backward compatible with existing daemon PID files and
also succeeds when no server was started, so rolling workers may use old or new
playbook content during promotion. Promote the locked environment first,
exercise a network-free game-reference preflight, then roll the playbook.

Rollback restores the previous playbook and lock together. Operators should
first confirm that no namespaced game server remains; stale PID files may be
removed only through the same ownership-confined cleanup path. A Pillow
downgrade must not be used as rollback while the older version lacks required
security fixes.

The motion-input check is stateless and wire-format neutral. Mixed old and new
workers can overlap during a zero-downtime rollout; the only behavior change is
that non-finite signatures deterministically produce `0.0` without a warning.
Rollback requires no drain, schema action, cache purge, or artifact cleanup.

The tiny-frame branch and hermetic fixture are deployment-neutral. Live
acquisition remains available behind the existing explicit environment switch,
so rollback needs no cache or schema migration; operators can re-enable the
network path without restarting Gludd.

The subprocess change is also wire-format and state neutral. Deploying workers
may overlap because each runner owns only children it created. Rollback needs no
drain or migration, but operators should confirm no namespaced game child is
live before replacing a worker; the old implementation may defer pipe release
until garbage collection.

## Observability and verification

The playbook emits explicit cleanup-started and cleanup-completed messages.
Missing PID files remain visible in task output without failing the play, while
attempted termination remains a distinct task result. Focused tests pin the
`stat`/`slurp` race boundary, terminal signal and removal paths, numeric PID
validation, the direct Pillow floor and hashed lock, and unchanged fail-closed
game verification.
Motion tests pin NaN and infinity to the neutral score under warnings-as-errors.
Lifecycle tests pin success, timeout, repeated cleanup, and delayed garbage
collection under warnings-as-errors, including closure of both parent-owned
subprocess pipes.
