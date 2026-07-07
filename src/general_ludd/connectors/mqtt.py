"""MQTT/Mosquitto broker source — subscribe to topics into a bounded ring buffer.

``MqttSource`` is the pub/sub analogue of :class:`~general_ludd.connectors.
webhook_buffer.WebhookBufferSource`: a background paho-mqtt client subscribes to
the configured topics and pushes each received message (normalized to the
connector record schema) into a bounded, thread-safe :class:`collections.deque`.
``query(spec)`` drains a snapshot of that buffer with the same ``kind``/``kinds``/
``since`` filters as every other source, plus a ``topic`` filter — so the
``/api/observe`` fan-out reads MQTT data exactly as it reads a Prometheus or a
webhook-buffer source, with no special-casing.

Two safety properties, matching the rest of the connector layer:

* **SSRF guard** — the broker host is checked against the shared deny-list
  (:func:`general_ludd.security.ssrf.host_is_blocked`) BEFORE any connection, so a
  misconfigured source cannot be steered at loopback / link-local / cloud-metadata
  / private endpoints.
* **Lazy dependency** — ``paho.mqtt`` is imported inside :meth:`connect` so this
  module (and its tests) import without the dependency present; install the
  ``mqtt`` extra to actually run a subscriber. Tests exercise the buffer/query/
  health logic by pushing records directly, with no broker.

Credentials are read from environment-variable NAMES (``username_env`` /
``password_env``) at connect time and never stored on the instance.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Mapping
from copy import deepcopy

from general_ludd.security.ssrf import host_is_blocked

logger = logging.getLogger(__name__)

_DEFAULT_KIND = "logs"
_DEFAULT_MAXLEN = 1000
_DEFAULT_PORT = 1883


class MqttBrokerBlockedError(RuntimeError):
    """Raised when the configured broker host is on the SSRF deny-list."""


class MqttSource:
    """Bounded, thread-safe ring of records received from MQTT topics (duck-typed source)."""

    KIND: str = _DEFAULT_KIND

    def __init__(self, config: dict[str, object] | None = None, **kwargs: object) -> None:
        # Support BOTH construction contracts: the ConnectorRegistry calls
        # ``Cls(config)`` with a single positional config dict (keys read off it),
        # while direct/test callers pass ergonomic keyword args. Keyword args win
        # over config-dict keys; unknown keys (e.g. the registry's "module") are
        # ignored so extra config is harmless.
        settings: dict[str, object] = {**(config or {}), **kwargs}
        name = str(settings.get("name") or "mqtt")
        broker_host = str(settings.get("broker_host") or "").strip()
        broker_port = int(str(settings.get("broker_port", _DEFAULT_PORT)))
        topics = settings.get("topics")
        maxlen = int(str(settings.get("maxlen", _DEFAULT_MAXLEN)))
        kind = str(settings.get("kind") or _DEFAULT_KIND)
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        if not broker_host:
            raise ValueError("broker_host is required")
        # SSRF guard: refuse a broker host on the deny-list (loopback, link-local,
        # cloud-metadata, private ranges) so a misconfigured source can't be
        # steered at an internal endpoint. Checked BEFORE any connection.
        if host_is_blocked(broker_host):
            raise MqttBrokerBlockedError(
                f"MQTT broker host {broker_host!r} is blocked by the SSRF deny-list"
            )
        self.name: str = name
        # Per-instance KIND (default 'logs') without mutating the class attr.
        self.KIND = kind
        self._broker_host = broker_host
        self._broker_port = broker_port
        # Default to the wildcard topic so an operator who omits `topics` still
        # receives everything, matching mosquitto_sub's default behavior.
        self._topics = list(topics) if isinstance(topics, list) else ["#"]
        self._maxlen = maxlen
        self._kind = kind
        self._username_env = settings.get("username_env")
        self._password_env = settings.get("password_env")
        self._client_id = str(settings.get("client_id") or "gludd-mqtt")
        self._qos = int(str(settings.get("qos", 0)))
        self._buffer: deque[dict[str, object]] = deque(maxlen=self._maxlen)
        self._lock = threading.Lock()
        self._client: object = None
        self._started = False
        # Guards lazy-connect so a failed first connect is not retried on every
        # query (see query()). The ConnectorRegistry builds sources but never
        # calls connect(), so query() starts the subscriber on first use.
        self._connect_attempted = False
        # Serializes the lazy-connect guard so two concurrent query() calls (the
        # observe fan-out is parallel) cannot both start a subscriber. Separate
        # from _lock so a slow connect() never blocks buffer reads/writes.
        self._connect_lock = threading.Lock()

    # -- capacity ---------------------------------------------------------- #
    @property
    def capacity(self) -> int:
        """The ring's maximum size (``maxlen``)."""
        return self._maxlen

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    # -- ingest (driven by the paho on_message callback) ------------------- #
    def _record_from_message(self, topic: str, payload: bytes) -> dict[str, object]:
        """Normalize one MQTT message into the connector record schema."""
        try:
            message = payload.decode("utf-8", errors="replace")
        except Exception:
            message = repr(payload)
        return {
            "ts": time.time(),
            "source": self.name,
            "kind": self._kind,
            "level_or_status": "info",
            "message": message,
            "value": None,
            "labels": {"topic": topic},
            "raw": {"topic": topic, "payload": message},
        }

    def push_message(self, topic: str, payload: bytes) -> None:
        """Normalize + buffer one MQTT message. Never raises (a broker callback
        must not be able to crash the network loop)."""
        try:
            record = self._record_from_message(topic, payload)
            with self._lock:
                self._buffer.append(record)
        except Exception as exc:  # defensive — keep the subscriber loop alive
            logger.warning("mqtt push failed for topic %s: %s", topic, exc)

    # -- read -------------------------------------------------------------- #
    def _ensure_connected(self) -> None:
        """Start the subscriber on first query (once). Best-effort — a missing
        paho dep or unreachable broker leaves the buffer empty, not an error, so
        the source degrades gracefully instead of failing the observe fan-out."""
        if self._started or self._connect_attempted:
            return
        with self._connect_lock:
            # Double-checked: another concurrent query() may have already run
            # (or attempted) the connect while we waited on the lock.
            if self._started or self._connect_attempted:
                return
            self._connect_attempted = True
            try:
                self.connect()
            except Exception as exc:  # missing dep / unreachable broker → stay empty
                logger.warning("mqtt lazy connect failed for %s: %s", self.name, exc)

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Return buffered records (deep-copied), filtered by ``spec``.

        On first call this lazily starts the background subscriber (the
        ConnectorRegistry never calls connect()); a failed start is logged once
        and the buffer is returned as-is. Supported filters: ``kind`` (equals),
        ``kinds`` (membership), ``topic`` (equals the record's ``labels.topic``),
        and ``since`` (numeric ``ts >=``; records with no ts are excluded when
        ``since`` is set). Never raises.
        """
        self._ensure_connected()
        spec = spec or {}
        with self._lock:
            snapshot = list(self._buffer)

        kind = spec.get("kind")
        kinds = spec.get("kinds")
        kinds_set: set[object] | None = set(kinds) if kinds is not None and isinstance(kinds, list) else None
        topic = spec.get("topic")
        since = spec.get("since")
        since_f: float | None = None
        if since is not None:
            try:
                since_f = float(str(since))
            except (TypeError, ValueError):
                since_f = None

        out: list[dict[str, object]] = []
        for record in snapshot:
            if kind is not None and record.get("kind") != kind:
                continue
            if kinds_set is not None and record.get("kind") not in kinds_set:
                continue
            if topic is not None:
                labels = record.get("labels")
                record_topic: object = None
                if isinstance(labels, Mapping):
                    record_topic = labels.get("topic")
                if record_topic != topic:
                    continue
            if since_f is not None:
                ts = record.get("ts")
                if not isinstance(ts, (int, float)) or float(ts) < since_f:
                    continue
            out.append(deepcopy(record))
        return out

    # -- health ------------------------------------------------------------ #
    def health(self) -> dict[str, object]:
        """Return a health dict. Never raises."""
        with self._lock:
            size = len(self._buffer)
        return {
            "ok": True,
            "source": self.name,
            "size": size,
            "capacity": self._maxlen,
            "connected": bool(self._started),
            "broker": f"{self._broker_host}:{self._broker_port}",
        }

    # -- lifecycle (background subscriber thread) -------------------------- #
    def connect(self) -> None:
        """Start the background paho-mqtt subscriber (idempotent).

        ``paho.mqtt`` is imported lazily here so the module imports without the
        dependency; install the ``mqtt`` extra to run a real subscriber.
        """
        if self._started:
            return
        try:
            import importlib

            # paho-mqtt: optional connector dep, guarded by try/except below
            mqtt = importlib.import_module("paho.mqtt.client")
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise RuntimeError(
                "paho-mqtt is not installed; install the 'mqtt' extra to run an MQTT source"
            ) from exc

        client = mqtt.Client(client_id=self._client_id)
        if self._username_env:
            user = os.environ.get(str(self._username_env)) if self._username_env else None
            pw = os.environ.get(str(self._password_env)) if self._password_env else None
            if user:
                client.username_pw_set(user, pw)

        def _on_connect(cl: object, userdata: object, flags: object, rc: object, *args: object) -> None:
            assert hasattr(cl, "subscribe")
            for topic in self._topics:
                cl.subscribe(topic, qos=self._qos)

        def _on_message(cl: object, userdata: object, msg: object) -> None:
            assert hasattr(msg, "topic") and hasattr(msg, "payload")
            self.push_message(msg.topic, msg.payload)

        client.on_connect = _on_connect
        client.on_message = _on_message
        try:
            client.connect(self._broker_host, self._broker_port)
            client.loop_start()  # spawns paho's own background network thread
        except Exception:
            # A partial start (raising connect, or a refused connection after
            # loop_start) must not leak the paho network thread — tear it down.
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
            raise
        self._client = client
        self._started = True

    def disconnect(self) -> None:
        """Stop the background subscriber (best-effort, never raises)."""
        client = self._client
        if client is not None:
            try:
                assert hasattr(client, "loop_stop") and hasattr(client, "disconnect")
                client.loop_stop()
                client.disconnect()
            except Exception:  # pragma: no cover - best-effort teardown
                pass
        self._started = False
