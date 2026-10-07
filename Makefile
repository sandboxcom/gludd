# Canonical GNU Make entrypoint.
# Targets and defaults live in explicit semantic fragments; order is API.
# Do not replace these declarations with a wildcard include.

include make/00-foundation.mk
include make/10-observability-and-tests.mk
include make/20-recovery-and-git.mk
include make/30-ci-and-release.mk
include make/40-cross-version-and-worktrees.mk
include make/50-development-and-guardrails.mk
include make/60-quality-packaging-and-sandbox.mk
include make/70-orchestration-and-model-runtime.mk
include make/80-agent-enforcement.mk
include make/90-infrastructure-and-services.mk
include make/95-collections-and-pipelines.mk
include make/99-azure-and-local-models.mk
