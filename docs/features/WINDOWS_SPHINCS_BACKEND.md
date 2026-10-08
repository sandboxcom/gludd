# Windows-safe SPHINCS+ backend

## Incident and scope

GitHub Actions run 32437385340 failed on the Windows 2022 packaging lane on
2026-08-20. The required `pyspx==0.5.0` dependency had no compatible Windows
wheel, so the installer attempted an MSVC source build. Haraka code in that
source uses variable-length arrays and expressions that MSVC does not accept as
compile-time constants. The same build also surfaced deprecated setuptools
license metadata and `test_require` configuration from the old source package.

Gludd now uses `pqcrypto` 0.4's PQClean-backed
`sphincs_shake_256s_simple` module. The public `slh_keygen`, `slh_sign`, and
`slh_verify` functions remain available. This is the SPHINCS+-SHAKE-256s-simple
category-five parameter family; retaining the historical `slh_*` names is not a
claim of FIPS 205 validation or certification.

## Dependency and packaging contract

The repository has one direct SPHINCS+ provider dependency: the locked
`pqcrypto>=0.4.0,<1` requirement. Its lock entry includes CPython 3.12 wheels for
Windows x86-64, macOS arm64, Linux x86-64, and Linux arm64. `pyspx` is absent
from both project metadata and the lock, so release hosts never fall back to its
warning-bearing source build.

The provider boundary preserves these SHAKE-256s dimensions:

| Value | Bytes |
|---|---:|
| Security parameter `n` | 32 |
| Public key | 64 |
| Secret key | 128 |
| Signature | 29,792 |

Verification rejects a signature of any other length before entering native
code. A correctly sized altered signature or altered message returns `False`.
Provider key generation remains randomized, and no private material is written
by the smoke test.

`gludd.spec` explicitly includes the Gludd adapter, pqcrypto's public Python
module, and its CFFI/native module. It continues to exclude Ansible controller
and collection code: those dependencies belong to Gludd's separately locked
execution environment, not the core executable.

The Windows job performs an immutable `uv sync --frozen`, then runs the reusable
public-API smoke script with Python warnings promoted to errors, before invoking
PyInstaller from the same frozen environment. The smoke performs exactly one
key generation, one signature, and two verifications. It opens no port, starts
no child process, and makes no network request. Success emits the searchable
`SPHINCS_BACKEND_SMOKE_PASS` marker with the algorithm and public sizes; an
exception or warning stops the artifact lane before publication.

## Zero-downtime rollout and rollback

1. Build the Windows artifact from the committed lock and require the SPHINCS+
   smoke marker before allowing PyInstaller to run.
2. Install and exercise the portable ZIP and NSIS artifact in the existing
   disposable Windows smoke roots before uploading either artifact.
3. Deploy a canary executable and route newly generated SPHINCS+ material to it
   only after the public round trip succeeds. Monitor backend-load, signing, and
   verification failures during the mixed-version window.
4. Do not assume old and new providers can exchange persisted keys or signatures
   merely because their byte sizes agree. Keep the originating provider version
   with existing material until explicit cross-provider vectors are approved;
   rotate keys as part of migration.
5. Roll back routing to the previous healthy executable on any mismatch. Keep at
   least one compatible worker for every unrotated key, and never silently fall
   back to another parameter set.

The smoke has constant operation count and bounded memory, so parallel platform
jobs do not multiply background services or leave cleanup state. The normal
workflow job timeout remains the outer resource bound.

## Upstream and practitioner evidence

- [PySPX 0.5.0 on PyPI](https://pypi.org/project/PySPX/) was released on
  2022-08-02 and publishes Linux wheels plus a source archive, but no Windows or
  macOS wheel. Its [upstream README](https://github.com/sphincs/pyspx) likewise
  tells non-Linux users to compile the extension locally.
- [pqcrypto 0.4.0 on PyPI](https://pypi.org/project/pqcrypto/) was released on
  2026-01-25 and publishes current CPython wheels for the supported Windows,
  macOS, and Linux release architectures. Its upstream build uses CFFI and
  PQClean rather than the stale PySPX packaging path.
- [PQClean's portability requirements](https://github.com/PQClean/PQClean)
  require clean builds on Windows, Linux, and macOS and explicitly prohibit
  variable-length arrays. This directly addresses the compiler construct that
  broke the Windows lane.
- A [Stack Overflow MSVC VLA report](https://stackoverflow.com/questions/60284657/why-msvc-doesnt-compile-variable-length-arrays)
  from 2020-02-18 records the same expected-constant-expression failure because
  MSVC does not implement C variable-length arrays.
- A [Python packaging forum thread](https://discuss.python.org/t/error-in-building-wheel-phase-when-using-pip-to-install-programs/16511)
  from 2022-06-14 documents the operational failure mode when a native package
  lacks a compatible wheel. A separate
  [Windows CFFI/PQClean thread](https://discuss.python.org/t/inconsistently-getting-error-1104-while-building-a-clib-cffi-extension-on-windows/44077)
  from 2024-01-24 reinforces why release consumers should install an upstream
  wheel instead of compiling cryptographic C sources during application setup.
- [NIST FIPS 205](https://csrc.nist.gov/pubs/fips/205/final), published
  2024-08-13, is the terminology and parameter reference. The release contract
  intentionally makes no module-validation claim.

## Verification evidence

- `tests/unit/test_windows_sphincs_backend.py` pins dependency, lock-wheel,
  parameter-size, PyInstaller, workflow, and public round-trip contracts.
- `tests/unit/test_sphincs_plus_deep.py` retains randomized signing, invalid-key,
  altered-message, altered-signature, and truncated-signature behavior coverage.
- `scripts/smoke_sphincs_backend.py` is shared by focused tests and the Windows
  packaging lane so CI does not substitute a weaker inline probe.
