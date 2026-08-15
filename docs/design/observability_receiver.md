# Design: Observability Receiver (the push side)

Issue #82. **Status: design only — not yet implemented.** This document is
implementation-ready and grounded in the code that already exists. Nothing here
is committed.

## 0. Context and motivation

gludd today has a **pull** model: concrete connectors implement the `Source`
Protocol (`src/general_ludd/connectors/base.py`), and `Observability.find()`
fans a query out across them. The debugging roles reach *out* to CloudWatch,
Loki, Tempo, etc., on demand.

This design adds the complementary **push** side: an in-process *receiver* that
**accepts telemetry pushed to gludd** by standard log shippers and agents
(OTLP exporters, syslog daemons, Fluent Bit/Fluentd, Graylog GELF senders,
Elastic Beats, or a plain webhook), normalizes each event into the *same*
record shape the pull connectors emit, and buffers it so the debugging roles /
`gludd_observe` can read recent telemetry without configuring an outbound
connector for every source.

The push side reuses, verbatim, the parsing and normalization that already
exist:

- **Parsers** — `src/general_ludd/connectors/ingest_formats.py` already provides
  pure, fail-soft, payload-bounded parsers:
  - `parse_fluent_forward(payload: bytes) -> list[dict]`
  - `parse_beats_lumberjack(frames: list[dict]) -> list[dict]`
  - `parse_gelf(payload: bytes) -> list[dict]`
  - plus the module constants `MAX_PAYLOAD_BYTES = 8 * 1024 * 1024` and
    `MAX_EVENTS = 100_000`, and the `_new_record(...)` shape builder.
  This design adds **one** new parser — `parse_otlp_logs` (and metric/trace
  siblings) — built in the same style in the same module.
- **Record shape** — every parser emits the canonical dict
  `{ts, source, kind, level_or_status, message, value, labels, raw}`, the same
  eight keys as `NormalizedRecord` / `normalized_record()` in
  `connectors/base.py`.
- **Cross-source join keys** — `normalize.normalize_join_keys(record)` folds the
  heterogeneous `labels` into the canonical `join` sub-dict
  (`trace_id` / `host` / `service` / `k8s` / `cloud` / `severity`). It is
  idempotent and total (never raises). The receiver runs every parsed record
  through it before buffering, so pushed telemetry correlates with pulled
  telemetry on identical keys.

The receiver is therefore mostly **transport + buffering + security glue**
around already-tested pure functions.

---

## 1. The receiver surface (HTTP + optional syslog)

### 1.1 How it mounts (matches the existing router convention)

Every router in `src/general_ludd/routers/` exports
`register(app: FastAPI, _daemon_state: dict[str, Any]) -> None` and decorates the
**app directly** (no `APIRouter` objects). `daemon.py` calls each
`register(app, _daemon_state)` after constructing
`app = FastAPI(title="General Ludd Agent", ...)`. The receiver follows this
exactly:

```python
# src/general_ludd/routers/receiver.py
def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    cfg = _daemon_state.get("receiver_config")     # ReceiverConfig | None
    if cfg is None or not cfg.enabled:
        return                                       # opt-in; mount nothing
    buffer = _daemon_state["receiver_buffer"]        # ReceiverBuffer (see §2)
    ...
    @app.post("/v1/logs")
    async def otlp_logs(request: Request) -> dict[str, Any]: ...
```

`daemon.py` gains one line alongside the existing `register` calls:
`receiver.register(app, _daemon_state)`. The buffer and config are placed into
`_daemon_state` in the daemon `_lifespan` (see §3.2).

### 1.2 HTTP endpoints

All endpoints are **POST** (telemetry ingest). All share the same pipeline
(§2). The `format` is determined by the route, never by sniffing untrusted
bytes for routing decisions.

| Route | Format | Body content-type | Parser |
|---|---|---|---|
| `POST /v1/logs` | OTLP/HTTP logs | `application/x-protobuf` or `application/json` | `parse_otlp_logs` (new, §2.2) |
| `POST /v1/metrics` | OTLP/HTTP metrics | same | `parse_otlp_metrics` (new) |
| `POST /v1/traces` | OTLP/HTTP traces | same | `parse_otlp_traces` (new) |
| `POST /ingest/webhook` | generic JSON | `application/json` | `parse_generic_webhook` (new, thin) |
| `POST /ingest/gelf` | GELF over HTTP | `application/json` (gelf doc) | `parse_gelf` (existing) |
| `POST /ingest/fluent` | Fluent Forward (JSON mode) | `application/json` | `parse_fluent_forward` (existing) |
| `POST /ingest/beats` | Beats/Lumberjack (decoded JSON window) | `application/json` | `parse_beats_lumberjack` (existing) |

Notes per endpoint:

- **OTLP/HTTP** uses the spec paths `/v1/{logs,metrics,traces}`. It accepts both
  protobuf and JSON encodings (the two OTLP/HTTP wire forms). On success OTLP
  expects an HTTP `200` with an (empty) `ExportServiceResponse` body; on partial
  rejection it expects a `partial_success` block. We return
  `{"partial_success": {"rejected": N, "error_message": "..."}}` JSON when
  `MAX_EVENTS` truncation or per-record drops occur, else `{}`.
- **/ingest/gelf** accepts a single GELF JSON document (HTTP GELF input does not
  chunk — chunking is UDP-only). `parse_gelf` already handles both plain and
  chunked bytes, so HTTP simply passes the request body through.
- **/ingest/fluent** accepts the JSON-mode `["tag", [[time, record], ...]]`
  array form; `parse_fluent_forward` already covers it (and falls back to
  guarded msgpack if the optional dep is present).
- **/ingest/beats** accepts an already-decoded JSON event list; transport
  decompression (lumberjack window frames) is out of scope for the HTTP path,
  matching the parser's documented contract.
- **/ingest/webhook** is the catch-all: a JSON object, or an array of objects,
  each mapped 1:1 to a record by `parse_generic_webhook` (best-effort field
  pulls: `message`/`msg`, `level`/`severity`, `timestamp`/`ts`/`@timestamp`,
  everything else into `labels`, original into `raw`).

### 1.3 Optional syslog listener (UDP/TCP)

There is **no** syslog/UDP code in the tree today (confirmed: no
`asyncio.start_server` / `DatagramProtocol` anywhere). Syslog is therefore an
**opt-in, off-by-default** addition, kept separate from the HTTP app because
syslog is connectionless/line-oriented, not request/response.

- Implemented as an asyncio `DatagramProtocol` (UDP, RFC 5426) and an
  `asyncio.start_server` line reader (TCP, RFC 6587 octet-framed or
  LF-delimited), started from the daemon `_lifespan` only when
  `receiver_config.syslog.enabled` is true.
- A new pure parser `parse_syslog(line: bytes) -> dict | None` handles RFC 3164
  and RFC 5424 framing, mapping `PRI` to a severity (reusing the
  `_SYSLOG_LEVELS` table already in `ingest_formats.py`) and the rest into
  `labels`. Same fail-soft contract: a malformed line returns `None`, never
  raises.
- Syslog has **no per-message auth** (the protocol carries none). It is gated by
  **bind address + network**, not tokens: bound to a loopback/private listen
  address (default `127.0.0.1`), documented as "trusted-network only," with the
  same per-message size cap and the same buffer backpressure as HTTP. UDP
  additionally drops (never buffers unbounded) on overflow.

### 1.4 Auth

The daemon already has a global `auth_and_stats_middleware` (in `daemon.py`):
it requires `Authorization: Bearer <GLUDD_PSK>` (constant-time
`hmac.compare_digest`) on every non-public, non-safe request, and **fails
closed** with `503 {"error":"auth_required"}` when `GLUDD_REQUIRE_AUTH` is set
but no PSK is configured. Public paths are an explicit allowlist
(`_PUBLIC_PATHS` + `/docs`) and `_is_public()` only exempts **safe** methods
(GET/HEAD/OPTIONS).

The receiver endpoints are all **POST**, so they are **never** treated as
public by `_is_public` and are protected by the daemon PSK middleware
automatically — no special-casing required. **The receiver routes MUST NOT be
added to `_PUBLIC_PATHS`.**

However, telemetry shippers usually need a *separate, narrower* credential than
the operator admin PSK (least privilege — a Fluent Bit instance should not hold
the key that can hit `/admin/...`). So the receiver layers an **ingest token**
on top:

- Config `receiver.ingest_token_envs: list[str]` holds env-var *names* (never
  inline secrets — see §4) whose values are valid ingest tokens.
- Each receiver endpoint checks the `Authorization: Bearer <ingest-token>` (or
  an `X-Gludd-Ingest-Token` header) against the resolved ingest tokens using
  `hmac.compare_digest`. A token may be scoped to a `source` label so multiple
  shippers get distinct, individually-revocable tokens.
- An ingest token grants **only** the receiver POST routes. It does not satisfy
  the admin PSK for any `/admin/...` route. The operator PSK, conversely, also
  works on receiver routes (admin is a superset), so a single-credential
  deployment still functions.
- If `receiver.ingest_token_envs` resolves empty *and* `GLUDD_PSK` is unset, the
  receiver **fails closed**: every ingest POST returns `503 auth_required`.
  (Same posture as the daemon's A-3 rule — no silent open ingest.)

### 1.5 Body-size limits, rate limiting, backpressure (DoS surface)

The receiver is, by definition, an attacker-reachable write endpoint, so each
control below is mandatory:

- **Body-size cap, enforced before allocation.** Read the request body with a
  hard ceiling of `receiver.max_payload_bytes` (default = the parsers'
  `MAX_PAYLOAD_BYTES`, 8 MiB). Reject with `413 Payload Too Large` *before*
  decoding. Enforced two ways: (a) honor `Content-Length` and reject early; (b)
  stream-read with a running byte counter and abort past the cap, so a lying or
  absent `Content-Length` cannot bypass it. The parsers *also* self-guard via
  `_too_big`, but the transport must cap first so we never buffer a 2 GiB body
  in memory just to hand it to a parser that will reject it.
- **Event-count cap.** The parsers already truncate at `MAX_EVENTS`
  (100 000) per payload; the receiver surfaces the truncation count in the
  `partial_success` response and a dropped-events metric.
- **Per-source rate limit.** A token-bucket limiter keyed by ingest-token (or by
  `source` label when tokens are shared), config
  `receiver.rate_limit_per_sec` (default e.g. 5000 records/s, burst
  10×). Over-limit requests get `429 Too Many Requests` with a `Retry-After`.
  This is in-process (single-worker — SQLite clamps gunicorn to 1 worker per
  `daemon.py`), so a simple per-token deque-of-timestamps or leaky bucket is
  sufficient; no shared store needed.
- **Backpressure on a full buffer.** When the bounded buffer (§2.3) is at
  capacity and its overflow policy is `reject` (not `drop_oldest`), the endpoint
  returns `503 Service Unavailable` + `Retry-After` rather than blocking the
  event loop. This propagates backpressure to well-behaved shippers (they retry)
  instead of letting the daemon OOM.
- **Decode bomb guard.** JSON parsing uses the stdlib `json` (already used by
  every parser) which is not vulnerable to billion-laughs; protobuf OTLP decode
  is length-prefixed and bounded by the body cap. We additionally cap nesting /
  field counts in the OTLP parser the same way the existing parsers cap
  `len(records) >= MAX_EVENTS`.

---

## 2. The pipeline: receive → parse → normalize → buffer

```text
HTTP body / syslog line
        │
        ▼
[1] transport guard   bind/auth/size/rate-limit  (§1.4, §1.5)
        │
        ▼
[2] parse             ingest_formats.parse_*  → list[dict] (canonical 8-key shape)
        │                                        fail-soft: bad input → []
        ▼
[3] normalize         normalize.normalize_join_keys(rec) → rec + canonical `join`
        │                                        idempotent, total, never raises
        ▼
[4] buffer            ReceiverBuffer.offer(rec) → accepted | dropped (overflow)
        │
        ▼
[5] consumers         gludd_observe / debugging roles drain via .recent()/.drain()
```

### 2.1 Step 2 — parse (reuse existing, add OTLP)

The router selects the parser by route (§1.2) and calls it with the request
bytes (or decoded JSON list). Because every parser is **pure and fail-soft**, a
hostile or malformed body yields `[]` (HTTP `400` "no records parsed" or `200`
with `rejected=N`), never a 500 or a raised exception. No new error-handling
machinery is needed in the router beyond mapping `[]` → a tidy response.

### 2.2 The one new parser: `parse_otlp_logs` (and metric/trace siblings)

Added to `connectors/ingest_formats.py`, in the same style as the existing
three (pure, fail-soft, `_too_big`-guarded, emits via `_new_record`, capped at
`MAX_EVENTS`). Contract:

```python
def parse_otlp_logs(payload: bytes, *, content_type: str = "application/json") -> list[dict[str, object]]:
    """Parse an OTLP/HTTP ExportLogsServiceRequest into normalized records.

    Accepts both OTLP encodings: protobuf (content_type application/x-protobuf)
    and JSON (application/json). Walks resourceLogs[].scopeLogs[].logRecords[];
    each logRecord becomes one record:

      ts              <- timeUnixNano / 1e9  (float epoch seconds)
      source          <- resource attribute service.name (else "otlp")
      level_or_status <- severityText (or severityNumber name)
      message         <- body.stringValue
      labels          <- merged resource + scope + record attributes, flattened
      raw             <- the logRecord dict

    Resource attributes (service.name, host.name, k8s.*, cloud.*, trace_id) land
    in labels so normalize_join_keys can fold them into canonical join keys.
    Fails soft to [] on any decode error; rejects payloads over MAX_PAYLOAD_BYTES.
    """
```

- **Protobuf path** requires the `opentelemetry-proto` (or generated stubs)
  dependency; like the msgpack branch in `_decode_fluent_payload`, it is
  **guarded** by a try/except `ImportError` and fails soft if absent, so the
  JSON path always works even without the proto dep installed. (The repo already
  pulls `opentelemetry-*` for `observability/otel_bridge.py`, so the proto types
  are likely available; the guard keeps the parser importable regardless.)
- `parse_otlp_metrics` and `parse_otlp_traces` follow the same walk over
  `resourceMetrics`/`resourceSpans`, setting `kind="metrics"`/`"traces"` and
  populating `value` (metric data point) / span-as-labels respectively, mapping
  OTLP `trace_id`/`span_id` into `labels` so `_TRACE_ALIASES` in `normalize.py`
  picks them up.
- These parsers are added to `ingest_formats.__all__`.

### 2.3 Step 4 — the buffer

There is already a precedent in-tree: `RecentTracesBuffer` in
`src/general_ludd/observability/trace_store.py` wraps `deque(maxlen=...)` with
`record()` / `recent(limit)` / `snapshot()` and a `_total_recorded` counter.
The receiver buffer is the same idea, generalized for the normalized-record
shape and overflow policy.

```python
# src/general_ludd/observability/receiver_buffer.py
@dataclass
class ReceiverBufferStats:
    accepted: int
    dropped: int
    spilled: int
    depth: int
    capacity: int

class ReceiverBuffer:
    """Bounded, in-memory ring of normalized records with an overflow policy.

    Loop-safe enough for a single-worker asyncio daemon (the SQLite path clamps
    gunicorn to one worker). Mirrors RecentTracesBuffer's deque approach.
    """
    def __init__(self, *, capacity: int, overflow: str = "drop_oldest",
                 spill_dir: Path | None = None) -> None: ...
    def offer(self, record: dict[str, object]) -> bool:  # True=accepted
    def recent(self, limit: int | None = None, *,
               source: str | None = None, kind: str | None = None) -> list[dict]:
    def drain(self, max_items: int | None = None) -> list[dict]:   # consume
    def stats(self) -> ReceiverBufferStats: ...
```

- **Backing store:** `deque(maxlen=capacity)` (config
  `receiver.buffer_capacity`, default 50 000 records).
- **Overflow policy** (`receiver.overflow`):
  - `drop_oldest` (default): `deque(maxlen=...)` evicts the oldest record;
    increment `dropped`. Newest telemetry wins — appropriate for live debugging.
  - `reject`: when full, `offer()` returns `False` and the endpoint emits the
    `503` backpressure response (§1.5).
  - `spill` (optional): when full and `spill_dir` is set, append-serialize the
    evicted record to a size-capped, rotated JSONL file under
    `receiver.spill_dir` (bounded by `receiver.spill_max_bytes`); increment
    `spilled`. Spill is **opt-in** and itself bounded — it must never grow
    unbounded on disk (disk-discipline: worktree venvs already pressure disk).
- **Retention:** records carry their `ts`; a periodic sweep (driven from the
  existing daemon event-loop tick, `GLUDD_TICK_INTERVAL`) evicts records older
  than `receiver.retention_seconds` (default 3600). Capacity is the hard bound;
  retention is the soft/time bound.
- **Consumers** (`gludd_observe` / debugging roles) read via `recent()` /
  `drain()`. `recent()` is non-destructive (snapshot for a role that wants the
  last N matching records); `drain()` is destructive (a streaming consumer that
  has acked the batch). Both accept `source`/`kind` filters so a role can ask
  for just `kind="logs"` from a given `source`. Because every buffered record
  already carries the canonical `join` sub-dict, a consumer can hand the batch
  straight to `Observability.associate(records, by="trace_id")` /
  `normalize.correlate(records, by=...)` to cluster pushed telemetry exactly as
  it does pulled telemetry.

---

## 3. Data model + wiring

### 3.1 Buffered record schema

Every buffered record is the canonical 8-key normalized dict **plus** the `join`
sub-dict and two receiver-added provenance keys:

```python
{
  "ts": float | int | str,        # as parsed; consumers may coerce to epoch
  "source": str,                  # tag/host/service.name; ingest-token scope may pin it
  "kind": "logs" | "metrics" | "traces" | "log",
  "level_or_status": str | int | None,
  "message": str,
  "value": float | None,          # metrics
  "labels": dict[str, Any],
  "raw": Any,                     # original decoded record
  "join": {                       # added by normalize_join_keys (idempotent)
     "trace_id"?: str, "host"?: str, "service"?: str,
     "k8s"?: {...}, "cloud"?: {...}, "severity"?: str
  },
  "_received_at": float,          # receiver wall-clock epoch (provenance / retention)
  "_ingest": "otlp"|"gelf"|"fluent"|"beats"|"webhook"|"syslog",  # which endpoint
}
```

`_received_at` and `_ingest` are the only receiver-only fields; they make
retention sweeps and debugging ("where did this come from?") trivial without
overloading `labels`. (`kind` value `"log"` comes from the existing
`_new_record` default; `"logs"/"metrics"/"traces"` come from the OTLP parsers
and align with `VALID_KINDS` in `base.py`.)

### 3.2 Daemon wiring

In `daemon.py` `_lifespan` (where the OTel bridge, budget guard, etc. are
already constructed), add — gated on `receiver_config.enabled`:

```python
recv_cfg = ReceiverConfig.from_user_config(user_config)   # §3.3
if recv_cfg.enabled:
    buffer = ReceiverBuffer(
        capacity=recv_cfg.buffer_capacity,
        overflow=recv_cfg.overflow,
        spill_dir=recv_cfg.spill_dir,
    )
    _daemon_state["receiver_config"] = recv_cfg
    _daemon_state["receiver_buffer"] = buffer
    if recv_cfg.syslog.enabled:
        await _start_syslog_listeners(recv_cfg, buffer)   # asyncio UDP/TCP
    # retention sweep is driven by the existing event-loop tick
```

`receiver.register(app, _daemon_state)` (added to the block of `register` calls
in `daemon.py`) mounts the HTTP routes and reads `receiver_config`/
`receiver_buffer` back out of `_daemon_state`, exactly like other routers read
their dependencies. The `RecentTracesBuffer`/metrics-exporter pattern of placing
shared singletons on `app.state` / `_daemon_state` is already established.

### 3.3 Config keys

Added under a new `receiver:` block in `general-ludd.yml` (loaded by
`UserConfig` in `config/user_config.py`, `env_prefix="GLUDD_"`,
`env_nested_delimiter="__"`, so e.g. `GLUDD_RECEIVER__ENABLED=true`):

```yaml
receiver:
  enabled: false                 # OFF by default — opt-in
  # HTTP receiver rides the daemon's existing FastAPI app/bind (host/port via CLI).
  ingest_token_envs: ["GLUDD_INGEST_TOKEN"]   # env-var NAMES that hold tokens
  max_payload_bytes: 8388608     # 8 MiB; defaults to MAX_PAYLOAD_BYTES
  rate_limit_per_sec: 5000
  buffer_capacity: 50000
  overflow: "drop_oldest"        # drop_oldest | reject | spill
  retention_seconds: 3600
  spill_dir: null                # set to enable disk spill
  spill_max_bytes: 268435456     # 256 MiB cap when spill enabled
  syslog:
    enabled: false
    udp_port: 5514               # non-privileged by default (not 514)
    tcp_port: 5514
    bind: "127.0.0.1"            # NEVER 0.0.0.0 by default
```

`ReceiverConfig` is a small pydantic model / dataclass with
`from_user_config(user_config)` that reads this block (mirroring how
`ObservabilityConfig` already nests under `UserConfig`). The HTTP receiver does
**not** introduce its own listen socket — it rides the daemon's existing
FastAPI app and its host/port (set via the daemon CLI `--host/--port`, validated
by `_validate_daemon_host/_port` in `daemon.py`). Only the syslog listener owns
its own bind/port.

### 3.4 Coexistence with the pull connectors

- The receiver does **not** register a `Source` in the `SourceRegistry`; pull
  sources answer `query(spec)` on demand, whereas the receiver is a continuous
  sink. They are orthogonal halves of one pipeline.
- A small adapter `ReceiverSource(LogSource)` MAY be provided that wraps the
  buffer's `recent(...)` behind the `Source.query(spec)` Protocol so that
  `Observability.find()` can fan a query across *both* pulled backends *and*
  recently-pushed telemetry in one call. This is optional sugar: it reads from
  the buffer, never writes, and `health()` reports `ReceiverBuffer.stats()`.
  This adapter is the single clean seam where push meets pull.
- Both sides produce identical records + `join` keys, so `correlate()` /
  `associate()` work uniformly across a mixed result set.

---

## 4. Security

The receiver is the only attacker-reachable *write* surface gludd exposes, so
security is the dominant concern, not an afterthought.

1. **Never bind `0.0.0.0` by default.** `receiver.syslog.bind` defaults to
   `127.0.0.1`. The HTTP receiver inherits the daemon's bind; `daemon.py`
   already auto-generates a random PSK when the daemon binds to a non-loopback
   interface, so an externally-reachable receiver always has auth. The config
   default and docs both state: external exposure is an explicit operator
   decision, paired with a token.
2. **Token auth, least privilege.** Ingest tokens (`receiver.ingest_token_envs`)
   are *separate* from the admin `GLUDD_PSK`; an ingest token cannot reach
   `/admin/...`. Tokens are read from **env-var names**, never inlined into YAML
   — this mirrors the hard invariant in `normalize.bundle_credentials` /
   `_iter_env_names` ("only env-var NAMES ever appear; no secret value is read").
   Comparison is constant-time (`hmac.compare_digest`), matching the daemon
   middleware. Multiple tokens allow per-shipper revocation.
3. **Fail closed.** No ingest token *and* no PSK ⇒ every ingest POST returns
   `503 auth_required` (the daemon's existing A-3 posture). There is no silent
   open-ingest mode.
4. **Payload caps everywhere.** Body size capped before allocation (§1.5);
   event count capped by `MAX_EVENTS`; buffer bounded by `buffer_capacity`;
   spill bounded by `spill_max_bytes`; rate limited per token. Every dimension
   that an attacker could grow is bounded.
5. **Not an open relay.** The receiver only *buffers* records for local
   consumers; it has **no forward/egress path** — it never re-emits received
   telemetry to any third party, so it cannot be abused as a reflector/relay.
   It performs **no outbound network I/O** as a result of ingest (the parsers
   are pure and do no I/O; the SSRF guard `is_safe_endpoint` in `base.py` stays
   on the *pull* side). The single optional outbound seam (`ReceiverSource`) is
   read-only and in-process.
6. **No `raw` execution / no SSTI.** `raw` is stored verbatim and only ever
   serialized back out; it is never templated or eval'd. (Contrast the
   ansible-render router, which deliberately sandboxes Jinja — the receiver has
   no such surface because it never renders.)
7. **Syslog trust boundary.** Syslog carries no auth; it is protected purely by
   loopback/private bind + the same size/rate/backpressure caps, and documented
   as trusted-network-only. UDP overflow **drops** (bounded), never queues
   unbounded.
8. **PSK/token never logged.** Following the daemon's A-2 rule, the receiver
   logs only *whether* a token matched, never any token bytes.

---

## 5. Test strategy (described — not written here; a gate is running)

Layered to match where the risk lives. All pure-function tests need no server.

### 5.1 Parser unit tests (pure, fast)

- **`parse_otlp_logs`** (new): a hand-built `ExportLogsServiceRequest` JSON
  fixture with two `resourceLogs`, nested scope/record attrs ⇒ assert N records,
  correct `ts` (nano→sec), `service.name`→`source`, `severityText`→
  `level_or_status`, resource `host.name`/`trace_id` landing in `labels`.
  Protobuf path tested only if the proto dep imports (guard mirrors the existing
  msgpack-guard test pattern); assert it fails soft to `[]` when absent.
- **Fail-soft contract** for every parser including OTLP: garbage bytes,
  truncated JSON, wrong top-level type, non-bytes input, and an oversized
  payload (`> MAX_PAYLOAD_BYTES`) each return `[]`/`None` and **never raise**.
- **`MAX_EVENTS` truncation**: a payload with > 100 000 logRecords yields exactly
  `MAX_EVENTS` records (parity with the existing fluent/beats cap tests).
- **`parse_syslog`** (new): RFC 3164 and RFC 5424 lines map `PRI`→severity via
  `_SYSLOG_LEVELS`; malformed line ⇒ `None`.
- Reuse/extend the existing `ingest_formats` tests for the unchanged parsers.

### 5.2 Normalize integration

- Feed each parser's output through `normalize_join_keys` and assert the `join`
  sub-dict carries the expected `trace_id`/`host`/`service`/`severity` for an
  OTLP record, a GELF record, a Fluent record — i.e. push telemetry lands on the
  same canonical keys as pull telemetry. Assert **idempotence** (normalize twice
  == once) and **totality** (a record with malformed `labels` ⇒ `join == {}`,
  no raise).

### 5.3 Buffer unit tests

- `offer/recent/drain/stats` happy path; `recent(source=, kind=)` filtering.
- **Overflow**: `drop_oldest` evicts oldest and bumps `dropped`; `reject` returns
  `False` at capacity; `spill` writes a bounded JSONL and bumps `spilled`,
  and **stops** at `spill_max_bytes` (assert file size ceiling).
- **Retention** sweep evicts records older than `retention_seconds` given a
  fake clock.
- Concurrency smoke: many `offer()` then a `drain()` under the single-worker
  model loses no accepted record and never exceeds `capacity`.

### 5.4 Endpoint / receiver router tests (FastAPI `TestClient`)

- **Auth**: POST without token ⇒ `401`/`503` (per config); with a valid ingest
  token ⇒ `200` and the buffer depth increments; admin PSK also accepted; an
  ingest token rejected on a sample `/admin/...` route (least-privilege
  assertion). Confirm receiver routes are **absent** from `_PUBLIC_PATHS`
  (a guardrail test, so no future edit silently makes ingest public).
- **Size cap**: body `> max_payload_bytes` ⇒ `413` *before* parse (assert the
  parser was not even reached, e.g. via a spy/monkeypatch).
- **Rate limit**: burst past `rate_limit_per_sec` ⇒ `429` + `Retry-After`.
- **Backpressure**: with `overflow="reject"` and a pre-filled buffer, ingest ⇒
  `503` + `Retry-After`, and the event loop is not blocked.
- **Per-route parsing**: a known OTLP/GELF/Fluent/Beats/webhook body ⇒ expected
  record count buffered with the right `_ingest` provenance and `kind`.
- **Partial success**: a payload over `MAX_EVENTS` ⇒ `200` with
  `partial_success.rejected == overflow_count`.

### 5.5 Syslog listener tests

- UDP datagram and TCP line each parsed and buffered; oversized datagram
  dropped; UDP overflow under a tiny buffer drops without raising; listener
  bound to `127.0.0.1` (assert the bind address, never `0.0.0.0`).

### 5.6 Coexistence / wiring tests

- With `receiver.enabled=false`, `register()` mounts **nothing** (the routes
  404) — opt-in proven.
- The optional `ReceiverSource` adapter satisfies the `Source` Protocol
  (`isinstance(..., LogSource)` via the runtime-checkable Protocol), its
  `query(spec)` returns buffered records, and `Observability.find()` merges
  pushed + pulled records sorted by `ts`.
- `correlate()` / `associate(by="trace_id")` cluster a mixed pushed+pulled set.

### 5.7 Security regression tests

- Fail-closed: no token + no PSK ⇒ every ingest route `503`.
- No-egress: monkeypatch/spy the HTTP client and assert ingest causes **zero**
  outbound requests (open-relay regression guard).
- Tokens never appear in logs (capture logs, assert no token substring).
