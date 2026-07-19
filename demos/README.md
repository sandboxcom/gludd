# gludd NF Feature Demos

Standalone, dependency-light demonstrations of the v0.1.0-beta.2 "NF" feature
work. Each demo is self-contained: no OpenBao daemon, no Firecracker binary, no
 HTTP API key, no real radio hardware required. External services are replaced
 with in-process fakes so the code paths exercised are the production code
 paths — only the I/O boundary is swapped.

## Layout

- `nf_features_demo.py` — single entry point with one subcommand per feature.

## Requirements

The gludd package must be importable. From a checkout, run via the project
venv:

    .venv/bin/python demos/nf_features_demo.py <feature>

or after `make init`:

    python demos/nf_features_demo.py <feature>

## Features

| Subcommand | Feature | Module exercised |
|------------|---------|------------------|
| `chat`     | NF.1 Chat CLI streaming formatter | `general_ludd.chat.formatter.StreamingChatFormatter` |
| `vm`       | NF.2 VM sandbox pool checkout     | `general_ludd.security.sandboxes.vm.pool.VMSandboxPool` |
| `sts`      | NF.7 STS token mint + quota check | `general_ludd.sts.minter.TokenMinter`, `general_ludd.sts.quotas.TokenQuotaEnforcer` |
| `entropy`  | NF.3 Binary RE entropy analysis   | `binary_re.plugins.module_utils.entropy_analyzer` |
| `aprs`     | NF.4 Radio APRS AX.25 decode      | `radio.plugins.module_utils.protocol_decoder.decode_aprs` |
| `corpus`   | NF.9 Language corpus analysis     | `general_ludd.language.corpus.CorpusAnalyzer` |

## Run all

    python demos/nf_features_demo.py all

Exit code is 0 iff every demo succeeded.
