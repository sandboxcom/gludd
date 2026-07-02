"""Tests for the MQTT/Mosquitto connector source (buffer + query + SSRF + lazy dep).

The buffer/query/health logic is exercised by pushing messages directly (no
broker); the subscriber wiring is exercised with a fake paho client injected via
sys.modules, so these tests need neither paho-mqtt installed nor a live broker.
"""

from __future__ import annotations

import sys
import time
import types

import pytest

from general_ludd.connectors.mqtt import MqttBrokerBlockedError, MqttSource


def _src(**over) -> MqttSource:
    kw = {"name": "mqtt-test", "broker_host": "broker.example.com", "maxlen": 10}
    kw.update(over)
    return MqttSource(**kw)


def test_push_and_query_returns_records() -> None:
    src = _src()
    src.push_message("sensors/temp", b"21.5")
    src.push_message("sensors/hum", b"48")
    got = src.query({})
    assert len(got) == 2
    assert {r["message"] for r in got} == {"21.5", "48"}
    assert all(r["source"] == "mqtt-test" for r in got)


def test_query_filters_by_topic() -> None:
    src = _src()
    src.push_message("a/b", b"one")
    src.push_message("c/d", b"two")
    got = src.query({"topic": "c/d"})
    assert [r["message"] for r in got] == ["two"]
    assert got[0]["labels"]["topic"] == "c/d"


def test_query_filters_by_since() -> None:
    src = _src()
    src.push_message("t", b"old")
    cutoff = time.time() + 0.01
    time.sleep(0.02)
    src.push_message("t", b"new")
    got = src.query({"since": cutoff})
    assert [r["message"] for r in got] == ["new"]


def test_query_filters_by_kind() -> None:
    src = _src(kind="logs")
    src.push_message("t", b"x")
    assert len(src.query({"kind": "logs"})) == 1
    assert src.query({"kind": "alerts"}) == []


def test_query_returns_copies_not_buffer_aliases() -> None:
    src = _src()
    src.push_message("t", b"x")
    got = src.query({})
    got[0]["labels"]["topic"] = "MUTATED"
    # Mutating the returned copy must not corrupt the buffer.
    assert src.query({})[0]["labels"]["topic"] == "t"


def test_maxlen_evicts_oldest() -> None:
    src = _src(maxlen=3)
    for i in range(5):
        src.push_message("t", str(i).encode())
    got = src.query({})
    assert len(got) == 3
    assert [r["message"] for r in got] == ["2", "3", "4"]


def test_health_reports_size_capacity_and_disconnected() -> None:
    src = _src(maxlen=7)
    src.push_message("t", b"a")
    health = src.health()
    assert health["ok"] is True
    assert health["source"] == "mqtt-test"
    assert health["size"] == 1
    assert health["capacity"] == 7
    assert health["connected"] is False
    assert health["broker"] == "broker.example.com:1883"


def test_bad_payload_never_raises() -> None:
    src = _src()
    src.push_message("t", b"\xff\xfe invalid utf8")  # decoded with errors=replace
    assert len(src.query({})) == 1


def test_invalid_maxlen_rejected() -> None:
    with pytest.raises(ValueError):
        _src(maxlen=0)


def test_missing_broker_host_rejected() -> None:
    with pytest.raises(ValueError):
        MqttSource(broker_host="")


def test_ssrf_blocks_loopback_broker() -> None:
    # host_is_blocked denies loopback — the source must refuse to construct.
    with pytest.raises(MqttBrokerBlockedError):
        MqttSource(broker_host="127.0.0.1")


def test_ssrf_blocks_metadata_broker() -> None:
    with pytest.raises(MqttBrokerBlockedError):
        MqttSource(broker_host="169.254.169.254")


def test_public_broker_host_constructs() -> None:
    # A non-blocked literal host constructs fine (no DNS resolution performed).
    src = MqttSource(broker_host="broker.hivemq.com")
    assert src.name == "mqtt"


class _FakeClient:
    """Minimal stand-in for paho.mqtt.client.Client capturing wiring."""

    def __init__(self, *a, **k) -> None:
        self.on_connect = None
        self.on_message = None
        self.subscribed: list[tuple[str, int]] = []
        self.connected_to: tuple[str, int] | None = None
        self.loop_started = False

    def username_pw_set(self, user, pw=None) -> None:
        self.creds = (user, pw)

    def subscribe(self, topic, qos=0) -> None:
        self.subscribed.append((topic, qos))

    def connect(self, host, port) -> None:
        self.connected_to = (host, port)

    def loop_start(self) -> None:
        self.loop_started = True

    def loop_stop(self) -> None:
        self.loop_started = False

    def disconnect(self) -> None:
        pass


def _install_fake_paho(monkeypatch, client_holder: list) -> None:
    mod = types.ModuleType("paho")
    client_mod = types.ModuleType("paho.mqtt.client")
    mqtt_mod = types.ModuleType("paho.mqtt")

    def _Client(*a, **k):
        c = _FakeClient(*a, **k)
        client_holder.append(c)
        return c

    client_mod.Client = _Client
    monkeypatch.setitem(sys.modules, "paho", mod)
    monkeypatch.setitem(sys.modules, "paho.mqtt", mqtt_mod)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", client_mod)


def test_connect_subscribes_and_on_message_buffers(monkeypatch) -> None:
    holder: list = []
    _install_fake_paho(monkeypatch, holder)
    src = _src(topics=["sensors/#"], qos=1)
    src.connect()
    assert src._started is True
    client = holder[0]
    assert client.connected_to == ("broker.example.com", 1883)
    assert client.loop_started is True
    # on_connect should subscribe to configured topics.
    client.on_connect(client, None, None, 0)
    assert ("sensors/#", 1) in client.subscribed
    # on_message should normalize + buffer the payload.
    msg = types.SimpleNamespace(topic="sensors/temp", payload=b"22.1")
    client.on_message(client, None, msg)
    got = src.query({"topic": "sensors/temp"})
    assert [r["message"] for r in got] == ["22.1"]
    assert src.health()["connected"] is True
    src.disconnect()
    assert src._started is False


def test_connect_is_idempotent(monkeypatch) -> None:
    holder: list = []
    _install_fake_paho(monkeypatch, holder)
    src = _src()
    src.connect()
    src.connect()
    assert len(holder) == 1  # second connect is a no-op


def test_registration_via_from_config() -> None:
    from general_ludd.connectors.registry import ConnectorRegistry

    reg = ConnectorRegistry.from_config(
        [
            {
                "name": "corp-mqtt",
                "kind": "logs",
                "module": "mqtt",
                "broker_host": "broker.example.com",
                "topics": ["app/#"],
            }
        ]
    )
    assert reg.errors() == {} or "corp-mqtt" not in reg.errors()
    src = reg.get("corp-mqtt")
    assert src is not None
    assert type(src).__name__ == "MqttSource"
    assert src.name == "corp-mqtt"
