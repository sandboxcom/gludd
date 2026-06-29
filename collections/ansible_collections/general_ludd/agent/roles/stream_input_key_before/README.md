# stream_input_key_before

Handles a single **pre-key** chunk emitted by `general_ludd.agent.gludd_stream`
when `dispatch_trigger.type=input_key` and `mode=before`.

## Vars injected by gludd_stream

| Var | Description |
|---|---|
| `stream_chunk` | Path to `artifact_dir/chunk-<index>.bin` for this dispatch |
| `stream_chunk_position` | Always `"before_key"` for this role |
| `stream_chunk_index` | 0-based dispatch counter |
| `stream_key_hit` | The matched key (UTF-8 if decodable, else hex) |

## Defaults

| Var | Default |
|---|---|
| `artifact_dir` | `/tmp/gludd-stream-result` |

## Behavior

Reads the chunk payload, writes a per-dispatch JSON manifest to
`{{ artifact_dir }}/dispatch-<index>-manifest.json`, and emits a debug line.
Report-only — never mutates the repo or the daemon. Suitable for both the
canned molecule scenario and real `gludd_stream` dispatches.
