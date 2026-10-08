# OpenCode Plugin Configuration Evidence

This note records externally reported, long-lived OpenCode plugin failures that affect
this repository's plugin-load and end-to-end configuration work. It is decision input,
not a replacement for the checked-in E2E tests.

## Recommended project posture

- Keep project plugins local and self-contained; do not add runtime dependencies unless
  the plugin test proves they resolve in a clean cache.
- Pin every third-party plugin to an explicit released version. Do not rely on
  `latest`, `local`, a range, or an implicit version.
- Run the OpenCode plugin E2E target under Node 26 with
  `--experimental-strip-types`; source plugins remain native TypeScript and must not
  use CommonJS `require()`.
- Treat plugin dependency installation as a startup-critical path. A failed external
  install must be surfaced by the E2E harness rather than masked by an already-warm
  developer cache.

## Forum and issue evidence

1. [Issue #2768: additional dependencies in custom tools](https://github.com/anomalyco/opencode/issues/2768)
   reports that dependencies placed below `.opencode/tool/` caused OpenCode to crash.
   The maintainer response says a package manifest in `.opencode/` or `.opencode/tool/`
   should work, but this is an older report and makes isolated E2E coverage important.

2. [Issue #11353: published plugin package with unresolved workspace references](https://github.com/anomalyco/opencode/issues/11353)
   documents an external install failure when `@opencode-ai/plugin@1.1.45` shipped
   `workspace:*` and `catalog:` dependencies. The report says OpenCode's isolated
   cache runs an install and startup halts when that install fails. This supports
   version pinning and a clean-cache plugin-load test.

3. [Issue #12143: unversioned plugin produces invalid SemVer](https://github.com/anomalyco/opencode/issues/12143)
   reports that an entry without `@<version>` can default to `latest`, then fail
   semver handling at startup. Explicit released versions are therefore required for
   all external plugin declarations.

4. [Issue #26003: released Desktop build requests `@opencode-ai/plugin@local`](https://github.com/anomalyco/opencode/issues/26003)
   traces repeated configuration dependency failures to a missing build-time version
   define. The proposed invariant is useful here: a release-mode configuration must
   resolve to a published version, while local development must not attempt to install
   a nonexistent `local` tag.

## Verification contract

The OpenCode configuration E2E suite should demonstrate all of these conditions:

- Node 26 loads every checked-in TypeScript plugin with
  `--experimental-strip-types`.
- `.opencode/scripts/verify-plugins.mjs` is checked in and invoked by the E2E
  suite; a missing verifier is a hard failure, never a skipped test.
- No plugin source uses `require()`; use ESM imports with explicit compatible paths.
- Plugin configuration contains no external entry without an explicit release version.
- The harness reports dependency-install and module-load failures as test failures.

## Sources

- [OpenCode configuration documentation](https://opencode.ai/docs/config/)
- [OpenCode plugin documentation](https://opencode.ai/docs/plugins/)
- [Node.js TypeScript documentation](https://nodejs.org/api/typescript.html)
