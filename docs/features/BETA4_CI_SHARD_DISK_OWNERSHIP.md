# Beta 4 CI shard disk ownership

## Contract

The canonical local and hosted shard runner owns every filesystem resource it
creates. Each shard receives one disposable Terraform provider cache, each
batch's pytest temporary tree is removed before the next batch starts, and a
batch is refused before launch when the owning filesystem has less than 2 GiB
free. The failure is observable as `SHARD-DISK-PREFLIGHT` and exit code 73.
There is no retry and no fallback to a user-global Terraform cache.

## 2026-09-01 incident

The exact beta 4 candidate
`a9372f33b52dd942f09b72331572393b58a55643` passed hosted Build and Release
(run 33453882627) and Molecule (run 33453882604). Its canonical local
Python 3.11 lane reached `unit-3b:batch-007` after the earlier shards had
passed, then Terraform failed to install
`hashicorp/azurerm v5.3.0` with `no space left on device`.

The runner retained every completed batch's temporary tree until the whole
shard ended. Terraform validation initialized multiple copied module
directories, so large provider binaries accumulated across batches. The owner
boundary now releases each batch immediately and shares provider downloads only
inside the current shard workspace.

## Upstream and practitioner evidence

HashiCorp documents that separate Terraform working directories normally
download separate provider copies and that providers may be hundreds of
megabytes. It documents `TF_PLUGIN_CACHE_DIR` as the supported per-process
override, requires the directory to exist, and notes that Terraform can use
symbolic links instead of copies:

- <https://developer.hashicorp.com/terraform/cli/config/config-file#provider-plugin-cache>
- <https://developer.hashicorp.com/terraform/cli/config/environment-variables#tf_plugin_cache_dir>

A practitioner report opened in 2025 describes multiple modules failing to
reuse a shared provider directory and explicitly asks that providers not be
downloaded for every module. It also records the important constraint that the
cache must not be configured as the provider installation directory itself:

- <https://github.com/hashicorp/terraform/issues/38376>

HashiCorp's older cross-platform cache report shows that lock-file hashes and
cache contents interact across architectures. Gludd therefore keeps the
existing lock files authoritative and does not enable
`TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE`:

- <https://github.com/hashicorp/terraform/issues/29958>

## ZDD and failure behavior

- Acquisition: the runner creates `terraform-plugin-cache` under the current
  shard workspace before its first batch.
- Use: all batch subprocesses receive the same exact
  `TF_PLUGIN_CACHE_DIR`; no user-global cache is inherited.
- Release: each pytest temporary tree is removed after its coverage fragment is
  copied; the shard workspace and cache are removed in the existing shard
  finalizer on success, failure, or cancellation.
- Zero-downtime behavior: no daemon or external deployment is restarted.
  Existing hosted and local jobs are immutable; a changed candidate must
  produce fresh exact-SHA attestations.
- Fail closed: disk observation errors and free space below 2 GiB stop before
  the next child starts. Later batches and shards are not launched.
- Observability: every preflight prints context, path, free bytes, required
  bytes, and status. Owned pytest and cleanup markers remain unchanged.

## Resource bounds

The runner remains single-worker and non-retrying. At most one Terraform
provider cache exists per active shard, one pytest process is active, and one
batch temporary tree is retained. The cache lifetime is bounded by the shard
finalizer and cannot outlive the runner's owned workspace.

## Rollback

Revert the focused runner commit. This restores the previous batch-lifetime
temporary ownership but invalidates beta 4 release evidence because the
ENOSPC regression will become RED. No schema, provider lock, state, or deployed
resource migration is required.

## 2026-10-15: honor the cache owner through every consumer

The retained `e914eeeb646d919e79fff4d6fdd30d6905bda479` gate's `other`
batch 24 used `/tmp/gludd-e6fc-j_vy7c9v/pytest`. During that batch, native
inventory measured its parent at 7.2 GiB and the volume rose from 95% to 99%.
This is a batch-level observation, not attribution of every byte to one test.

The all-stacks fixture copied the populated `.plugin-cache` along with source
inputs, and six Terraform test consumers replaced the runner's cache selection
with the checkout cache. The shared `terraform_test_environment` boundary now
preserves a nonempty inherited `TF_PLUGIN_CACHE_DIR`, creates only that selected
directory, and copies rather than mutates the parent environment. Outside the
runner, the existing checkout fallback remains when the variable is absent or
empty. A temporary write probe checks the selected directory's actual access
before Terraform runs and removes only its own file. Invalid, unwritable or full
selected caches fail; they are not silently replaced or treated as offline skips.

The E2E source copy excludes `.plugin-cache` as well as its existing generated
file exclusions. Production Azure VM and Container App materialization excludes
only `.terraform` and `.plugin-cache` entries from copied module/stack inputs.
Canonical HCL, production lock files, source trees and destination-owned
Terraform state remain intact. No serving resource is restarted or deleted.

The new small-fixture regressions first failed on cache duplication, consumer
wiring and generated-provider copying. The combined focused suite then passed
228 cases; the dedicated coverage profile measured 94% combined coverage with
both measured files above 75%. This is source acceptance, not complete gate,
exact-SHA hosted, deployment acceptance or an observed peak-disk reduction.
All real init, validate and plan checks and their existing assertions remain.

The [March 2023 practitioner report](https://discuss.hashicorp.com/t/how-to-set-a-common-terraform-folder-instead-of-individual-ones/50881)
describes provider duplication exceeding 30 GB. The [concurrent cache issue](https://github.com/hashicorp/terraform/issues/31964)
and [official cache documentation](https://developer.hashicorp.com/terraform/cli/config/config-file#provider-plugin-cache)
support retaining per-shard ownership rather than introducing a global writable
cache. No provider mirror, lock-file bypass, alternate scanner or new runner
is added. Existing batch and shard cleanup owners remain responsible for release.
