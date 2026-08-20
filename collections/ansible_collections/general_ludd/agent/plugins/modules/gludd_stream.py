#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_stream
  short_description: Stream input signals (video/audio/text/binary) and dispatch buffered chunks to a cloned role
  description:
    - Opens a device (e.g. C(/dev/video0), C(hw:0,0), C(pulse), C(rtsp://...),
      a file path) via the appropriate capture tool (ffmpeg / tail / cat) and
      streams bytes into a rolling in-memory buffer.
    - When the configured C(dispatch_trigger) fires (size threshold, interval,
      silence detection, or external MQ message), the current buffer is written
      to C(artifact_dir/chunk-<n>.bin) and POSTed to the daemon's
      C(/admin/stream/dispatch) endpoint.
    - The dispatch clones the *calling role* (the role invoking this module),
      injects the chunk as a named variable, and runs the clone on that chunk.
      An optional C(external_processor) (whisper.cpp / ffmpeg / agent) is
      invoked on the chunk before the cloned role's tasks run.
    - The module stops when the C(stop_condition) fires (timeout, EOF, external
      MQ message, or max dispatches), drains the remaining buffer with a final
      dispatch, closes the device subprocess, and returns.
    - Check mode skips device capture and HTTP dispatch; it returns a synthetic
      result describing the would-have-run pipeline.
  options:
    device:
      description: Input device path or URL (e.g. C(/dev/video0), C(hw:0,0), C(rtsp://...)).
      type: str
      required: true
    device_kind:
      description: Hint for which capture tool / external processor to use.
      type: str
      required: true
      choices: [video, audio, text, binary]
    buffer_size:
      description: Maximum bytes/frames to retain in the rolling buffer before discarding oldest.
      type: int
      default: 1048576
    dispatch_trigger:
      description: >
        Dict describing when to dispatch the current buffer. Exactly one of
        C(size_threshold), C(interval), C(silence_detection), C(external),
        C(input_key).
      type: dict
      suboptions:
        type:
          description: Trigger kind.
          type: str
          choices: [size_threshold, interval, silence_detection, external, input_key]
        bytes:
          description: Threshold in bytes (type=size_threshold).
          type: int
        seconds:
          description: Interval or timeout in seconds (type=interval).
          type: float
        min_silence_ms:
          description: Minimum silence length in ms (type=silence_detection).
          type: int
        topic:
          description: MQ topic whose first message triggers dispatch (type=external).
          type: str
        key:
          description: >
            The input key to match in the byte stream (type=input_key). Either a
            UTF-8 string (encoded to bytes for matching) or a list of byte ints
            for binary streams.
          type: raw
        mode:
          description: >
            How to dispatch around the key (type=input_key).
            C(before) dispatches the buffer accumulated up to (excluding) the key.
            C(after) dispatches the buffer accumulated past the key when the next
            key fires or C(max_bytes_after) is reached.
            C(both) dispatches BOTH the pre-key buffer and the post-key buffer as
            two separate dispatches sharing the same C(stream_chunk_index).
          type: str
          choices: [before, after, both]
          default: before
        max_bytes_before:
          description: Cap on the pre-key buffer (type=input_key).
          type: int
          default: 1048576
        max_bytes_after:
          description: >
            Cap on the post-key buffer; flushes the after-chunk when reached
            (type=input_key, modes after/both).
          type: int
          default: 1048576
        include_key_in_after:
          description: >
            When true, the matched key bytes are prepended to the dispatched
            after_key payload (type=input_key). The key is always consumed from
            the working search buffer so repeated keys delimit successive chunks.
          type: bool
          default: true
    dispatch_role_clone:
      description: >
        Dict controlling role cloning. When C(clone_current_role=true), the
        daemon clones the calling role and injects the buffered chunk.
      type: dict
      suboptions:
        clone_current_role:
          description: Whether to clone the current calling role.
          type: bool
          default: true
        inject_as:
          description: Variable name the chunk path is injected as.
          type: str
          default: stream_chunk
        extra_vars:
          description: Additional vars passed to the cloned role.
          type: dict
          default: {}
    external_processor:
      description: >
        Optional tool the cloned role invokes on the chunk before its own tasks.
      type: dict
      suboptions:
        tool:
          description: Tool name.
          type: str
          choices: [whisper.cpp, ffmpeg, agent]
        args:
          description: Extra args passed to the tool.
          type: list
          elements: str
          default: []
        timeout_seconds:
          description: Per-chunk processor timeout.
          type: int
          default: 60
    stop_condition:
      description: Dict describing when to stop listening.
      type: dict
      suboptions:
        type:
          description: Stop kind.
          type: str
          choices: [timeout, eof, external, max_dispatches]
        seconds:
          description: Timeout in seconds (type=timeout).
          type: float
        topic:
          description: MQ topic whose first message stops the stream (type=external).
          type: str
        count:
          description: Max dispatches before stopping (type=max_dispatches).
          type: int
    daemon_url:
      description: Base URL of the gludd daemon.
      type: str
      required: true
    psk:
      description: Pre-shared key for daemon auth.
      type: str
      default: ""
      no_log: true
    artifact_dir:
      description: Directory to write buffer chunks before dispatch. Defaults to tempfile.gettempdir().
      type: str
      default: ""

EXAMPLES:
  # Example 1: audio transcription via whisper.cpp
  - name: Stream microphone and transcribe speech
    general_ludd.agent.gludd_stream:
      device: "hw:0,0"
      device_kind: audio
      buffer_size: 524288
      dispatch_trigger:
        type: silence_detection
        min_silence_ms: 800
      dispatch_role_clone:
        clone_current_role: true
        inject_as: stream_chunk
      external_processor:
        tool: whisper.cpp
        args: ["--language", "en"]
        timeout_seconds: 60
      stop_condition:
        type: timeout
        seconds: 300
      daemon_url: "http://localhost:8000"
      psk: "{{ gludd_psk }}"
    register: stream_out

  - name: Use the last transcription task id
    ansible.builtin.debug:
      msg: "transcription task: {{ stream_out.last_task_id }}"

  # Example 2: video feature detection via an agent
  - name: Stream webcam and detect features per chunk
    general_ludd.agent.gludd_stream:
      device: "/dev/video0"
      device_kind: video
      buffer_size: 2097152
      dispatch_trigger:
        type: size_threshold
        bytes: 2097152
      dispatch_role_clone:
        clone_current_role: true
        inject_as: stream_chunk
        extra_vars:
          detection_target: "person"
      external_processor:
        tool: agent
        timeout_seconds: 120
      stop_condition:
        type: max_dispatches
        count: 5
      daemon_url: "http://localhost:8000"
      psk: "{{ gludd_psk }}"
    register: stream_out

  # Example 3: text log tail with interval dispatch
  - name: Tail an app log and dispatch every 10s
    general_ludd.agent.gludd_stream:
      device: "/var/log/app.log"
      device_kind: text
      buffer_size: 1048576
      dispatch_trigger:
        type: interval
        seconds: 10
      dispatch_role_clone:
        clone_current_role: true
        inject_as: stream_chunk
      stop_condition:
        type: eof
      daemon_url: "http://localhost:8000"
      psk: "{{ gludd_psk }}"
    register: stream_out

  # Example 4: input-key trigger — dispatch the lines BEFORE each TRIGGER marker
  - name: Stream a log and dispatch the lead-up to each TRIGGER marker
    general_ludd.agent.gludd_stream:
      device: "/tmp/fixture.log"
      device_kind: binary
      dispatch_trigger:
        type: input_key
        key: "TRIGGER"
        mode: before
      dispatch_role_clone:
        clone_current_role: true
        inject_as: stream_chunk
      stop_condition:
        type: eof
      daemon_url: "http://localhost:8000"
      psk: "{{ gludd_psk }}"
    register: stream_out

  # Example 5: input-key dual mode — dispatch BOTH the pre-key and post-key
  # chunks per key hit (two cloned roles, same stream_chunk_index, different
  # stream_chunk_position). The cloned role branches on stream_chunk_position.
  - name: Audio VAD — dispatch speech (before_key) + silence/continuation (after_key)
    general_ludd.agent.gludd_stream:
      device: "hw:0,0"
      device_kind: audio
      dispatch_trigger:
        type: input_key
        key: "{{ silence_marker_bytes }}"
        mode: both
        max_bytes_after: 65536
        include_key_in_after: true
      dispatch_role_clone:
        clone_current_role: true
        inject_as: stream_chunk
      stop_condition:
        type: timeout
        seconds: 600
      daemon_url: "http://localhost:8000"
      psk: "{{ gludd_psk }}"
    register: stream_out

RETURN:
  dispatches:
    description: Number of dispatches performed.
    type: int
    returned: success
  bytes_processed:
    description: Total bytes streamed through the buffer.
    type: int
    returned: success
  last_task_id:
    description: task_id returned by the final dispatch.
    type: str
    returned: success
  stopped_by:
    description: Which stop_condition fired (timeout, eof, external, max_dispatches).
    type: str
    returned: success
  chunks:
    description: List of artifact chunk paths written to artifact_dir.
    type: list
    elements: str
    returned: success
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from contextlib import suppress
from typing import Any, cast

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd_stream_buffer import (
    RollingBuffer,
)

_VALID_TRIGGER_TYPES = {
    "size_threshold",
    "interval",
    "silence_detection",
    "external",
    "input_key",
}
_VALID_STOP_TYPES = {"timeout", "eof", "external", "max_dispatches"}
_VALID_INPUT_KEY_MODES = {"before", "after", "both"}

_TRIGGER_REQUIRED_KEYS = {
    "size_threshold": ("bytes",),
    "interval": ("seconds",),
    "silence_detection": ("min_silence_ms",),
    "external": ("topic",),
    "input_key": ("key",),
}

_STOP_REQUIRED_KEYS = {
    "timeout": ("seconds",),
    "eof": (),
    "external": ("topic",),
    "max_dispatches": ("count",),
}


def _validate_dispatch_trigger(trigger: Any) -> str | None:
    """Return an error string if ``trigger`` is malformed, else None."""
    if not isinstance(trigger, dict):
        return "dispatch_trigger must be a dict"
    ttype = trigger.get("type")
    if ttype not in _VALID_TRIGGER_TYPES:
        return (
            f"dispatch_trigger.type {ttype!r} not in "
            f"{sorted(_VALID_TRIGGER_TYPES)}"
        )
    for key in _TRIGGER_REQUIRED_KEYS[ttype]:
        if key not in trigger:
            return f"dispatch_trigger type={ttype!r} requires key {key!r}"
    if ttype == "input_key":
        mode = trigger.get("mode", "before")
        if mode not in _VALID_INPUT_KEY_MODES:
            return (
                f"dispatch_trigger input_key.mode {mode!r} not in "
                f"{sorted(_VALID_INPUT_KEY_MODES)}"
            )
        key_field = trigger.get("key")
        if not isinstance(key_field, (str, list)) or (
            isinstance(key_field, list)
            and not all(isinstance(b, int) for b in key_field)
        ):
            return "dispatch_trigger input_key.key must be a str or list[int]"
    return None


def _validate_stop_condition(cond: Any) -> str | None:
    """Return an error string if ``cond`` is malformed, else None."""
    if not isinstance(cond, dict):
        return "stop_condition must be a dict"
    stype = cond.get("type")
    if stype not in _VALID_STOP_TYPES:
        return (
            f"stop_condition.type {stype!r} not in "
            f"{sorted(_VALID_STOP_TYPES)}"
        )
    for key in _STOP_REQUIRED_KEYS[stype]:
        if key not in cond:
            return f"stop_condition type={stype!r} requires key {key!r}"
    return None


def _device_command(device: str, kind: str) -> list[str]:
    """Return the argv that captures ``device`` for ``kind``.

    The command writes raw stream bytes to stdout; the module reads them
    incrementally.
    """
    if kind == "video":
        return ["ffmpeg", "-i", device, "-f", "matroska", "-"]
    if kind == "audio":
        return ["ffmpeg", "-f", "alsa", "-i", device, "-f", "wav", "-"]
    if kind == "text":
        return ["tail", "-f", device]
    if kind == "binary":
        return ["cat", device]
    raise ValueError(f"unknown device_kind {kind!r}")


def _encode_key(key: Any) -> bytes:
    """Encode an input-key spec to bytes.

    ``key`` may be a UTF-8 string (text/binary streams) or a list of byte
    ints (binary streams). Existing bytes/bytearray values pass through.
    """
    if isinstance(key, str):
        return key.encode("utf-8")
    if isinstance(key, (bytes, bytearray)):
        return bytes(key)
    if isinstance(key, list):
        if not all(isinstance(b, int) and 0 <= b <= 255 for b in key):
            raise ValueError("input_key.key list elements must be ints in [0, 255]")
        return bytes(key)
    raise TypeError(f"input_key.key must be str or list[int], got {type(key)!r}")


def _key_label(key_bytes: bytes) -> str:
    """Human-readable label for the matched key: UTF-8 if decodable, else hex."""
    try:
        return key_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return key_bytes.hex()


def _write_chunk(artifact_dir: str, index: int, payload: bytes) -> str:
    """Write ``payload`` to ``artifact_dir/chunk-<index>.bin`` and return the path."""
    os.makedirs(artifact_dir, exist_ok=True)
    path = os.path.join(artifact_dir, f"chunk-{index}.bin")
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


def _dispatch(
    client: GluddClient,
    role: str,
    inject_as: str,
    artifact_path: str,
    extra_vars: dict[str, Any] | None,
    processor: dict[str, Any] | None,
    position: str = "single",
    key_hit: str = "",
    chunk_index: int = 0,
) -> dict[str, Any]:
    """POST /admin/stream/dispatch and return the response dict.

    The cloned role receives, as extra_vars:
      - ``inject_as``          (e.g. stream_chunk) -> artifact path
      - ``stream_chunk_position`` -> "before_key" / "after_key" / "single"
      - ``stream_key_hit``       -> the matched key (UTF-8 if decodable, else hex)
      - ``stream_chunk_index``   -> Nth dispatch overall (per key-hit for both mode)
    """
    body: dict[str, Any] = {
        "role": role,
        "extra_vars": dict(extra_vars or {}),
        "processor": processor or {},
    }
    body["extra_vars"][inject_as] = artifact_path
    body["extra_vars"]["stream_chunk_position"] = position
    body["extra_vars"]["stream_key_hit"] = key_hit
    body["extra_vars"]["stream_chunk_index"] = chunk_index
    return cast(
        dict[str, Any],
        client.post("/admin/stream/dispatch", body=body),
    )


def _check_status(
    module: Any,
    resp: dict[str, Any],
    label: str,
    ok_codes: tuple[int, ...] = (200, 201),
) -> bool:
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return False
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return False
    if status_code not in ok_codes:
        msg = resp.get("detail") or f"HTTP {status_code}"
        module.fail_json(**error_result(f"{label} failed: {msg}", status=status_code))
        return False
    return True


def _trigger_fires(
    trigger: dict[str, Any],
    buffer: RollingBuffer,
    start_ts: float,
    last_dispatch_ts: float,
) -> bool:
    """Return True if ``trigger`` should fire given the current state."""
    ttype = trigger.get("type")
    if ttype == "size_threshold":
        return len(buffer) >= int(trigger.get("bytes", 0))
    if ttype == "interval":
        return (time.monotonic() - last_dispatch_ts) >= float(trigger.get("seconds", 0))
    if ttype == "silence_detection":
        # Silence detection requires webrtcvad/whisper; if unavailable, fall
        # back to a size-threshold of half the buffer so we still make progress.
        return bool(len(buffer) >= buffer.max_bytes // 2)
    if ttype == "external":
        # External triggers are polled separately; the streaming loop checks
        # the MQ inbox. This function returns False to keep streaming.
        return False
    if ttype == "input_key":
        # Input-key triggers are handled by _input_key_step in the main loop;
        # this function returns False so the generic path does not also fire.
        return False
    return False


class _InputKeyState:
    """Mutable state for the input-key trigger state machine.

    The state machine is driven one push at a time by ``_input_key_step``,
    which mutates ``buffer`` and this state and returns the list of dispatch
    descriptors (payload, position, chunk_index) the caller must dispatch.
    """

    __slots__ = (
        "include_key_in_after",
        "key_bytes",
        "key_index",
        "max_bytes_after",
        "max_bytes_before",
        "mode",
        "post_key_active",
    )

    def __init__(
        self,
        mode: str,
        key_bytes: bytes,
        include_key_in_after: bool = True,
        max_bytes_after: int = 1048576,
        max_bytes_before: int = 1048576,
    ) -> None:
        self.mode = mode
        self.key_bytes = key_bytes
        self.include_key_in_after = include_key_in_after
        self.max_bytes_after = max_bytes_after
        self.max_bytes_before = max_bytes_before
        self.key_index = -1
        self.post_key_active = False


def _tail_after_key(buffer: RollingBuffer, offset: int, key_len: int) -> bytes:
    """Reset ``buffer`` to the bytes AFTER the key and return the consumed key+tail-prefix.

    Always advances past the key so the next ``find_key`` cannot re-match it.
    """
    _head, tail = buffer.split_at(offset)
    # tail now starts at the key; drop the key bytes themselves.
    after = tail[key_len:]
    buffer.drain()
    buffer.push(after)
    return bytes(tail[:key_len])  # the key bytes (used to prepend to after-chunks)


def _input_key_step(state: _InputKeyState, buffer: RollingBuffer) -> list[tuple[bytes, str, int]]:
    """Process the buffer for one input-key trigger iteration.

    Returns a list of ``(payload, position, chunk_index)`` descriptors for the
    caller to dispatch. Mutates ``buffer`` and ``state``.

    The matched key is ALWAYS consumed from the working buffer (the search
    advances past it) so repeated keys delimit successive chunks correctly.
    When ``include_key_in_after`` is true the key bytes are prepended to the
    dispatched "after_key" payload so the downstream role still sees them.
    """
    dispatches: list[tuple[bytes, str, int]] = []
    key = state.key_bytes
    klen = len(key)
    mode = state.mode
    pos = buffer.find_key(key)

    def _maybe_after_prefix(payload: bytes) -> bytes:
        if state.include_key_in_after:
            return key + payload
        return payload

    if mode == "before":
        if pos is not None:
            head, _tail = buffer.split_at(pos)
            # Drop the key from the working buffer (advance past it).
            remaining = _tail[klen:]
            buffer.drain()
            buffer.push(remaining)
            state.key_index += 1
            if head:
                dispatches.append((head, "before_key", state.key_index))
        return dispatches

    if mode == "after":
        if state.post_key_active:
            if pos is not None:
                head, _tail = buffer.split_at(pos)
                remaining = _tail[klen:]
                buffer.drain()
                buffer.push(remaining)
                if head:
                    state.key_index += 1
                    dispatches.append((_maybe_after_prefix(head), "after_key", state.key_index))
                else:
                    # empty inter-key span; stay active without dispatching
                    pass
            elif len(buffer) >= state.max_bytes_after:
                payload = buffer.drain()
                state.post_key_active = False
                if payload:
                    state.key_index += 1
                    dispatches.append((_maybe_after_prefix(payload), "after_key", state.key_index))
        else:
            if pos is not None:
                state.key_index += 1
                _tail_after_key(buffer, pos, klen)
                state.post_key_active = True
        return dispatches

    if mode == "both":
        if pos is not None:
            head, _tail = buffer.split_at(pos)
            remaining = _tail[klen:]
            buffer.drain()
            buffer.push(remaining)
            if state.post_key_active:
                if head:
                    dispatches.append((_maybe_after_prefix(head), "after_key", state.key_index))
                    state.key_index += 1
                    dispatches.append((head, "before_key", state.key_index))
                else:
                    state.key_index += 1
            else:
                if head:
                    state.key_index += 1
                    dispatches.append((head, "before_key", state.key_index))
                else:
                    state.key_index += 1
            state.post_key_active = True
        elif state.post_key_active and len(buffer) >= state.max_bytes_after:
            payload = buffer.drain()
            state.post_key_active = False
            if payload:
                dispatches.append((_maybe_after_prefix(payload), "after_key", state.key_index))
        return dispatches

    return dispatches


def _input_key_drain_final(state: _InputKeyState, buffer: RollingBuffer) -> list[tuple[bytes, str, int]]:
    """Flush any remaining accumulated post-key bytes at stop time."""
    dispatches: list[tuple[bytes, str, int]] = []
    if state.mode in ("after", "both") and state.post_key_active and len(buffer) > 0:
        payload = buffer.drain()
        state.post_key_active = False
        if payload:
            dispatches.append((payload, "after_key", state.key_index))
    return dispatches


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            device=dict(type="str", required=True),
            device_kind=dict(
                type="str",
                required=True,
                choices=["video", "audio", "text", "binary"],
            ),
            buffer_size=dict(type="int", default=1048576),
            dispatch_trigger=dict(type="dict", default=None),
            dispatch_role_clone=dict(type="dict", default=None),
            external_processor=dict(type="dict", default=None),
            stop_condition=dict(type="dict", default=None),
            daemon_url=dict(type="str", required=True),
            psk=dict(type="str", default="", no_log=True),
            artifact_dir=dict(type="str", default=""),
        ),
        supports_check_mode=True,
    )

    device: str = module.params["device"]
    kind: str = module.params["device_kind"]
    buffer_size: int = module.params["buffer_size"]
    trigger = module.params["dispatch_trigger"] or {"type": "size_threshold", "bytes": buffer_size}
    role_clone = module.params["dispatch_role_clone"] or {}
    processor = module.params["external_processor"]
    stop_cond = module.params["stop_condition"] or {"type": "eof"}
    daemon_url: str = module.params["daemon_url"]
    psk: str = module.params["psk"]
    artifact_dir: str = module.params["artifact_dir"] or tempfile.gettempdir()

    # Validate trigger / stop shapes before doing any work.
    err = _validate_dispatch_trigger(trigger)
    if err:
        module.fail_json(**error_result(err))
        return
    err = _validate_stop_condition(stop_cond)
    if err:
        module.fail_json(**error_result(err))
        return

    inject_as = role_clone.get("inject_as", "stream_chunk")
    extra_vars = role_clone.get("extra_vars", {}) or {}

    # Current role name — from the ansible_role_name fact injected by ansible.
    role_name = os.environ.get("GLUDD_ROLE_NAME", "") or extra_vars.get("_role_name", "")

    if module.check_mode:
        module.exit_json(
            **ok_result(
                {
                    "dispatches": 0,
                    "bytes_processed": 0,
                    "last_task_id": "",
                    "stopped_by": "check_mode",
                    "chunks": [],
                    "msg": "check_mode: stream capture skipped",
                },
                changed=False,
            )
        )
        return

    client = GluddClient(base_url=daemon_url, psk=psk, timeout=60)

    buffer = RollingBuffer(max_bytes=buffer_size)
    chunks: list[str] = []
    dispatch_count = 0
    bytes_processed = 0
    last_task_id = ""
    stopped_by = "eof"

    # Input-key state machine (only constructed for type=input_key triggers).
    key_state: _InputKeyState | None = None
    key_label = ""
    if trigger.get("type") == "input_key":
        key_bytes = _encode_key(trigger.get("key"))
        key_label = _key_label(key_bytes)
        key_state = _InputKeyState(
            mode=trigger.get("mode", "before"),
            key_bytes=key_bytes,
            include_key_in_after=bool(trigger.get("include_key_in_after", True)),
            max_bytes_after=int(trigger.get("max_bytes_after", 1048576)),
            max_bytes_before=int(trigger.get("max_bytes_before", 1048576)),
        )

    def _do_dispatch(payload: bytes, position: str, chunk_index: int) -> bool:
        """Write chunk + POST dispatch. Returns False if module already failed."""
        nonlocal dispatch_count, last_task_id
        artifact_path = _write_chunk(artifact_dir, dispatch_count, payload)
        chunks.append(artifact_path)
        resp = _dispatch(
            client,
            role_name,
            inject_as,
            artifact_path,
            extra_vars,
            processor,
            position=position,
            key_hit=key_label,
            chunk_index=chunk_index,
        )
        if not _check_status(module, resp, "stream dispatch"):
            return False
        last_task_id = resp.get("task_id", "")
        dispatch_count += 1
        return True

    start_ts = time.monotonic()
    last_dispatch_ts = start_ts
    max_dispatches = None
    if stop_cond.get("type") == "max_dispatches":
        max_dispatches = int(stop_cond.get("count", 0))
    timeout_seconds = None
    if stop_cond.get("type") == "timeout":
        timeout_seconds = float(stop_cond.get("seconds", 0))

    # Open the capture subprocess.
    try:
        cmd = _device_command(device, kind)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        module.fail_json(**error_result(f"capture tool unavailable: {exc}"))
        return
    except Exception as exc:
        module.fail_json(**error_result(f"failed to open device {device!r}: {exc}"))
        return

    try:
        assert proc.stdout is not None
        while True:
            # Check stop conditions.
            now = time.monotonic()
            if timeout_seconds is not None and (now - start_ts) >= timeout_seconds:
                stopped_by = "timeout"
                break
            if max_dispatches is not None and dispatch_count >= max_dispatches:
                stopped_by = "max_dispatches"
                break

            chunk = proc.stdout.read(4096)
            if not chunk:
                if stop_cond.get("type") == "eof":
                    stopped_by = "eof"
                    break
                # For non-eof stop conditions, EOF ends the loop regardless.
                stopped_by = "eof"
                break

            buffer.push(chunk)
            bytes_processed += len(chunk)

            if key_state is not None:
                while True:
                    step_dispatches = _input_key_step(key_state, buffer)
                    if not step_dispatches:
                        break
                    for payload, position, chunk_index in step_dispatches:
                        if not _do_dispatch(payload, position, chunk_index):
                            return
                continue

            if _trigger_fires(trigger, buffer, start_ts, last_dispatch_ts):
                payload = buffer.drain()
                if not _do_dispatch(payload, "single", dispatch_count):
                    return
                last_dispatch_ts = time.monotonic()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            with suppress(Exception):
                proc.kill()

    # Final drain dispatch if any bytes remain.
    if key_state is not None:
        for payload, position, chunk_index in _input_key_drain_final(key_state, buffer):
            if not _do_dispatch(payload, position, chunk_index):
                return
    elif len(buffer) > 0:
        payload = buffer.drain()
        if not _do_dispatch(payload, "single", dispatch_count):
            return

    module.exit_json(
        **ok_result(
            {
                "dispatches": dispatch_count,
                "bytes_processed": bytes_processed,
                "last_task_id": last_task_id,
                "stopped_by": stopped_by,
                "chunks": chunks,
            },
            changed=dispatch_count > 0,
        )
    )


if __name__ == "__main__":
    main()
