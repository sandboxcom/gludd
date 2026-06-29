# stream_input_key_both

Handles chunks emitted by `general_ludd.agent.gludd_stream` when
`dispatch_trigger.type=input_key` and `mode=both`.

## Vars injected by gludd_stream

| Var | Description |
|---|---|
| `stream_chunk` | Path to `artifact_dir/chunk-<index>.bin` for this dispatch |
| `stream_chunk_position` | `"before_key"` (lead-up) or `"after_key"` (continuation) |
| `stream_chunk_index` | Per-key-hit counter (shared by the before/after pair) |
| `stream_key_hit` | The matched key (UTF-8 if decodable, else hex) |

## Defaults

| Var | Default |
|---|---|
| `artifact_dir` | `/tmp/gludd-stream-result` |

## Behavior

Branches on `stream_chunk_position` and writes a per-dispatch JSON manifest
to `{{ artifact_dir }}/dispatch-<index>-{before,after}-manifest.json`, plus a
debug line. Report-only — never mutates the repo or the daemon. Suitable for
both the canned molecule scenario and real `gludd_stream` dispatches.
