# MCP tool-topic sharding

**Status:** IMPLEMENTED

## Problem

The generated `docs/MCP_TOOLS_TOPICS.yml` artifact exceeded the repository's
2,500-line limit. Splitting the file with a YAML tag was rejected: YAML 1.2.2
defines documents and streams, but no portable filesystem include mechanism.
A long-lived PyYAML user request from May 2022 likewise shows that `!include`
requires a custom constructor and application-specific path handling.

- [YAML 1.2.2 specification](https://yaml.org/spec/1.2.2/)
- [PyYAML issue #632: how to include YAML files?](https://github.com/yaml/pyyaml/issues/632)

## Contract

`docs/MCP_TOOLS_TOPICS.yml` is a small, versioned manifest. Its ordered `parts`
entries name files below `docs/mcp-tool-topics/` and pin each shard's SHA-256
and topic count. `scripts/mcp_topics.py` is the shared writer/loader. The writer
sorts topic IDs, keeps every shard below 2,500 lines, writes shards before the
manifest, and preserves the original in-memory mapping. The loader fails closed
on path traversal, symlinks escaping the manifest directory, unknown fields,
missing or malformed files, invalid UTF-8/YAML, digest/count drift, duplicates,
and out-of-order topic IDs.

The generator, generated-artifact hygiene check, and feature inventory consume
this contract. Consumers needing a monolithic mapping call `load_topics()`;
they do not concatenate YAML text or register a global PyYAML constructor.

## ZDD and rollback

Generation writes each file through a sibling temporary file and atomically
replaces it. The root manifest is published last, so readers see either the old
complete set or the new complete set. A failed load does not partially return
topics. To roll back, revert the generator, loader, consumers, manifest, and
shards in one commit, then regenerate the prior monolithic artifact.

Validation is provided by `make gen-mcp-tools`, `make mcp-docs-check`,
`make check-generated-artifact-hygiene`, focused MCP/feature-inventory tests,
and `make check-file-line-limits FILE_LINE_LIMIT_POLICY=config/file_line_limits.json`.
