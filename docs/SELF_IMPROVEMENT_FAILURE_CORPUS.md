# Self-improvement failure corpus

The failure corpus turns expensive local-model incidents into deterministic,
offline regression cases. It exercises the same compact proposal gateway, batch
decoder, strict parent merge, and typed retry sanitizer used by the live
self-improvement flow. It does not load, download, or start a model.

Run the pinned corpus with an explicit input:

```console
make test-self-improve-failure-corpus SELF_IMPROVE_FAILURE_CORPUS_FILE=config/self-improve/failure-corpus.json
```

The target emits one bounded case marker followed by one deterministic JSON
summary. A schema error exits 2. A replay that no longer matches its expected
typed result exits 1. Neither failure mode echoes model output or native logs.

## Pinned incident classes

| Case | Boundary replayed | Required result |
| --- | --- | --- |
| `no-op-replace` | Compact proposal expansion | Reject identical old and new text |
| `multiline-redundant-metadata` | Compact proposal expansion | Reject model-owned parent metadata |
| `token-exhaustion` | Structured completion decode | Classify a length stop as `decode_budget` |
| `worker-success-parent-merge-rejection` | Worker encode, parent decode, strict merge | Prove worker and batch decode succeed before scope rejection |
| `raw-native-log-leakage` | Validation retry sanitizer | Select the stable error marker and exclude native logs |

The tracked JSON contains synthetic, reviewed inputs only. It records no real
secret, home-directory path, prompt, repository excerpt, or complete native
log. Even the leakage case uses invented native-log phrases and asserts that
those phrases cannot appear in published feedback.

## Why these cases are durable

The upstream
[llama.cpp grammar documentation](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
states that a JSON schema constrains generation but is not itself shown to the
model, so the expected shape must also be described in the prompt. Gludd
therefore keeps both a compact prompt contract and a strict parent decoder; the
redundant-metadata case pins that separation.

Two long-lived practitioner threads show why successful grammar setup is not
the same as a complete usable object:

- A llama.cpp user reported JSON grammar output reaching the prediction limit
  after repeated legal whitespace, with `stopped_limit` rather than an
  end-of-sequence stop in
  [discussion 6277](https://github.com/ggml-org/llama.cpp/discussions/6277).
- llama-cpp-python users discussed incomplete JSON and the difficulty of
  treating an intermediate structured stream as a valid final result in
  [discussion 1615](https://github.com/abetlen/llama-cpp-python/discussions/1615).

A separate issue remained active across multiple releases because
`verbose=False` could still leave model metadata and backend messages on
stderr. That practitioner evidence is recorded in
[llama-cpp-python issue 1245](https://github.com/abetlen/llama-cpp-python/issues/1245).
The corpus consequently verifies output sanitization instead of assuming native
logging stays quiet.

## Zero-downtime and lifecycle boundary

This is a zero-downtime check: it does not mutate application state, contact the
network, acquire a model lease, create a daemon, touch the database, or change a
Git worktree. Each gateway replay uses a uniquely namespaced temporary
directory, and Python's temporary-directory context removes it on every normal
or exceptional exit. The fixture chat object lives only in memory.

The offline target complements rather than replaces live acceptance. Live tests
still prove acquisition, llama.cpp compatibility, model quality, process
ownership, and cleanup. Running the corpus first catches known parser and
sanitizer regressions in seconds, before those expensive lifecycle checks.

## Adding a regression

For every new live-model failure:

1. Reduce the observation to the smallest synthetic input that reproduces the
   same parent-side behavior. Never copy raw prompts, paths, secrets, or logs.
2. Add a failing unit test and a versioned fixture case with exact
   `type`, `source`, and `detail` expectations.
3. Add every unsafe fragment to `forbidden_substrings`.
4. Run the offline target before another live-model attempt.
5. Keep the live failure class only when it exercises a distinct boundary; do
   not accumulate model-specific duplicates.

This red-first workflow preserves fast feedback while the live daemon and any
unrelated deployments continue serving traffic.
