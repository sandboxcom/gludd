"""Deep tests for PAKE protocols: SPAKE2+ (RFC 9383) and OPAQUE (RFC 9381)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from general_ludd.algorithms import pake as pake_module
from general_ludd.algorithms.pake import (
    OPAQUEClient,
    OPAQUEConfig,
    OPAQUERegistration,
    OPAQUEServer,
    PAKEError,
    SPAKE2PlusClient,
    SPAKE2PlusGroup,
    SPAKE2PlusServer,
)

# ── SPAKE2+ reference test vectors (RFC 9383) ── not yet used in assertions ──


class TestSPAKE2PlusGroup:
    def test_group_p256(self):
        g = SPAKE2PlusGroup.P256()
        assert g.name == "P-256"
        assert g.hash_name == "sha256"
        assert g.point_bytes == 65
        assert g.scalar_bytes == 32

    def test_group_p384(self):
        g = SPAKE2PlusGroup.P384()
        assert g.name == "P-384"
        assert g.hash_name == "sha384"
        assert g.point_bytes == 97
        assert g.scalar_bytes == 48

    def test_group_p521(self):
        g = SPAKE2PlusGroup.P521()
        assert g.name == "P-521"
        assert g.point_bytes == 133
        assert g.scalar_bytes == 66

    def test_p521_field_parameters_match_nist(self):
        group = SPAKE2PlusGroup.P521()
        params = pake_module._group_params(group)

        assert params["p"] == (1 << 521) - 1
        assert params["a"] == params["p"] - 3
        assert (params["p"].bit_length() + 7) // 8 == group.scalar_bytes

    def test_group_immutable(self):
        g = SPAKE2PlusGroup.P256()
        with pytest.raises(AttributeError):
            g.hash_name = "sha512"


class TestSPAKE2PlusClientServer:
    def test_full_exchange_p256(self):
        group = SPAKE2PlusGroup.P256()
        password = b"test-password-42"
        client_id = b"alice"
        server_id = b"bob"
        context = b"test-context"

        server = SPAKE2PlusServer(group, password, server_id, client_id, context)
        client = SPAKE2PlusClient(group, password, client_id, server_id, context)

        msg_server = server.start()
        msg_client = client.finish(msg_server)
        server_key = server.finish(msg_client)

        assert server_key == client.get_shared_secret()
        assert len(server_key) == 32
        assert client.get_shared_secret() == server_key

    def test_full_exchange_p384(self):
        group = SPAKE2PlusGroup.P384()
        password = b"strong-password-384"
        client_id = b"alice"
        server_id = b"bob"
        context = b"SPAKE2+-384-test"

        server = SPAKE2PlusServer(group, password, server_id, client_id, context)
        client = SPAKE2PlusClient(group, password, client_id, server_id, context)

        msg_server = server.start()
        msg_client = client.finish(msg_server)
        server_key = server.finish(msg_client)

        assert server_key == client.get_shared_secret()

    @pytest.mark.timeout(10)
    def test_full_exchange_p521(self):
        group = SPAKE2PlusGroup.P521()
        password = b"p521-password-test"
        client_id = b"client"
        server_id = b"server"
        context = b""

        server = SPAKE2PlusServer(group, password, server_id, client_id, context)
        client = SPAKE2PlusClient(group, password, client_id, server_id, context)

        msg_server = server.start()
        msg_client = client.finish(msg_server)
        server_key = server.finish(msg_client)

        assert len(msg_server) == group.point_bytes
        assert len(msg_client) == group.point_bytes
        assert len(server_key) == group.scalar_bytes
        assert server_key == client.get_shared_secret()

    def test_wrong_password_fails(self):
        group = SPAKE2PlusGroup.P256()
        context = b"auth"

        server = SPAKE2PlusServer(group, b"correct", b"server", b"client", context)
        client = SPAKE2PlusClient(group, b"wrong!", b"client", b"server", context)

        msg_server = server.start()
        msg_client = client.finish(msg_server)
        server_key = server.finish(msg_client)

        assert server_key != client.get_shared_secret()

    def test_deterministic_same_password(self):
        group = SPAKE2PlusGroup.P256()
        pw = b"deterministic"

        server1 = SPAKE2PlusServer(group, pw, b"s", b"c", b"ctx")
        client1 = SPAKE2PlusClient(group, pw, b"c", b"s", b"ctx")

        server2 = SPAKE2PlusServer(group, pw, b"s", b"c", b"ctx")
        client2 = SPAKE2PlusClient(group, pw, b"c", b"s", b"ctx")

        s1 = server1.start()
        c1 = client1.finish(s1)
        k1 = server1.finish(c1)

        s2 = server2.start()
        c2 = client2.finish(s2)
        k2 = server2.finish(c2)

        assert k1 == client1.get_shared_secret()
        assert k2 == client2.get_shared_secret()

    def test_different_ids_different_keys(self):
        group = SPAKE2PlusGroup.P256()
        pw = b"same-password"

        sa = SPAKE2PlusServer(group, pw, b"server", b"alice", b"ctx")
        ca = SPAKE2PlusClient(group, pw, b"alice", b"server", b"ctx")

        sb = SPAKE2PlusServer(group, pw, b"server", b"bob", b"ctx")
        cb = SPAKE2PlusClient(group, pw, b"bob", b"server", b"ctx")

        s_a = sa.start()
        c_a = ca.finish(s_a)
        k_a = sa.finish(c_a)

        s_b = sb.start()
        c_b = cb.finish(s_b)
        k_b = sb.finish(c_b)

        assert k_a != k_b

    def test_different_contexts_different_keys(self):
        group = SPAKE2PlusGroup.P256()
        pw = b"same-password"

        s1 = SPAKE2PlusServer(group, pw, b"s", b"c", b"ctx1")
        c1 = SPAKE2PlusClient(group, pw, b"c", b"s", b"ctx1")
        s2 = SPAKE2PlusServer(group, pw, b"s", b"c", b"ctx2")
        c2 = SPAKE2PlusClient(group, pw, b"c", b"s", b"ctx2")

        srv1 = s1.start()
        cli1 = c1.finish(srv1)
        k1 = s1.finish(cli1)
        srv2 = s2.start()
        cli2 = c2.finish(srv2)
        k2 = s2.finish(cli2)

        assert k1 != k2

    def test_multiple_exchanges_same_instance(self):
        group = SPAKE2PlusGroup.P256()

        for _ in range(3):
            server = SPAKE2PlusServer(group, b"pw", b"s", b"c", b"ctx")
            client = SPAKE2PlusClient(group, b"pw", b"c", b"s", b"ctx")
            s_msg = server.start()
            c_msg = client.finish(s_msg)
            k_s = server.finish(c_msg)
            assert k_s == client.get_shared_secret()
            assert len(k_s) == 32

    def test_empty_password(self):
        group = SPAKE2PlusGroup.P256()
        server = SPAKE2PlusServer(group, b"", b"s", b"c", b"ctx")
        client = SPAKE2PlusClient(group, b"", b"c", b"s", b"ctx")

        s_msg = server.start()
        c_msg = client.finish(s_msg)
        k_s = server.finish(c_msg)

        assert k_s == client.get_shared_secret()

    def test_long_password(self):
        group = SPAKE2PlusGroup.P256()
        pw = b"x" * 1000
        server = SPAKE2PlusServer(group, pw, b"s", b"c", b"ctx")
        client = SPAKE2PlusClient(group, pw, b"c", b"s", b"ctx")

        s_msg = server.start()
        c_msg = client.finish(s_msg)
        k_s = server.finish(c_msg)

        assert k_s == client.get_shared_secret()


class TestSPAKE2PlusRejects:
    def test_reject_empty_client_id(self):
        group = SPAKE2PlusGroup.P256()
        with pytest.raises(PAKEError, match="client_id"):
            SPAKE2PlusServer(group, b"pw", b"server", b"", b"ctx")

    def test_reject_empty_server_id(self):
        group = SPAKE2PlusGroup.P256()
        with pytest.raises(PAKEError, match="server_id"):
            SPAKE2PlusClient(group, b"pw", b"client", b"", b"ctx")


class TestSPAKE2PlusEdgeCases:
    def test_finish_before_start_raises(self):
        group = SPAKE2PlusGroup.P256()
        server = SPAKE2PlusServer(group, b"pw", b"s", b"c", b"ctx")
        with pytest.raises(PAKEError, match="start"):
            server.finish(b"\x00" * 65)


class TestHashToPointBounds:
    @pytest.mark.timeout(2)
    def test_search_fails_closed_after_bounded_attempts(self, monkeypatch: pytest.MonkeyPatch):
        digest = Mock()
        digest.digest.return_value = b"\x00" * 32
        sha256 = Mock(return_value=digest)
        monkeypatch.setattr(pake_module.hashlib, "sha256", sha256)
        non_residue_params = {"p": 3, "a": 0, "b": 2, "gx": 1, "gy": 1, "n": 2}

        with pytest.raises(PAKEError, match="256 attempts"):
            pake_module._hash_to_point(b"never-maps", non_residue_params, 1)

        assert sha256.call_count == 256


# ── OPAQUE tests ─────────────────────────────────────────────────────────


class TestOPAQUERegistration:
    def test_register_and_complete_exchange_p256(self):
        config = OPAQUEConfig(curve="P-256")
        password = b"user-password-123"
        user_id = b"alice@example.com"
        server_id = b"server.example.com"

        record = OPAQUERegistration.register(config, password, user_id, server_id)
        assert "envelope" in record
        assert "server_public_key" in record
        assert "oprf_seed" in record
        assert len(record["server_public_key"]) > 0
        assert record["envelope"] is not None

        server = OPAQUEServer(config, record)
        client = OPAQUEClient(config, password, user_id, server_id)

        msg1 = client.start()
        msg2 = server.start(msg1)
        msg3 = client.finish(msg2)
        server_key = server.finish(msg3)

        assert server_key == client.get_shared_secret()
        assert len(server_key) == 32

    def test_register_and_exchange_p384(self):
        config = OPAQUEConfig(curve="P-384")
        password = b"opaque-pwd-384"
        user_id = b"user@domain"
        server_id = b"auth.example.com"

        record = OPAQUERegistration.register(config, password, user_id, server_id)
        server = OPAQUEServer(config, record)
        client = OPAQUEClient(config, password, user_id, server_id)

        msg1 = client.start()
        msg2 = server.start(msg1)
        msg3 = client.finish(msg2)
        sk = server.finish(msg3)

        assert sk == client.get_shared_secret()

    def test_register_and_exchange_ed25519(self):
        config = OPAQUEConfig(curve="ed25519")
        password = b"edwards-password"
        user_id = b"ed25519-user"
        server_id = b"ed25519-server"

        record = OPAQUERegistration.register(config, password, user_id, server_id)
        server = OPAQUEServer(config, record)
        client = OPAQUEClient(config, password, user_id, server_id)

        msg1 = client.start()
        msg2 = server.start(msg1)
        msg3 = client.finish(msg2)
        sk = server.finish(msg3)

        assert sk == client.get_shared_secret()

    def test_wrong_password_rejected(self):
        config = OPAQUEConfig(curve="P-256")
        user_id = b"user"
        server_id = b"server"

        record = OPAQUERegistration.register(config, b"correct-password", user_id, server_id)
        server = OPAQUEServer(config, record)
        client = OPAQUEClient(config, b"wrong-password", user_id, server_id)

        msg1 = client.start()
        msg2 = server.start(msg1)
        with pytest.raises(PAKEError, match="envelope"):
            client.finish(msg2)
        assert True

    def test_different_users_different_keys(self):
        config = OPAQUEConfig(curve="P-256")

        rec_a = OPAQUERegistration.register(config, b"pw", b"alice", b"srv")
        rec_b = OPAQUERegistration.register(config, b"pw", b"bob", b"srv")

        srv_a = OPAQUEServer(config, rec_a)
        cli_a = OPAQUEClient(config, b"pw", b"alice", b"srv")
        srv_b = OPAQUEServer(config, rec_b)
        cli_b = OPAQUEClient(config, b"pw", b"bob", b"srv")

        m1a = cli_a.start()
        m2a = srv_a.start(m1a)
        m3a = cli_a.finish(m2a)
        ka = srv_a.finish(m3a)

        m1b = cli_b.start()
        m2b = srv_b.start(m1b)
        m3b = cli_b.finish(m2b)
        kb = srv_b.finish(m3b)

        assert ka != kb

    def test_multiple_registrations_distinct(self):
        config = OPAQUEConfig(curve="P-256")

        rec1 = OPAQUERegistration.register(config, b"pwd1", b"user1", b"srv")
        rec2 = OPAQUERegistration.register(config, b"pwd2", b"user2", b"srv")

        assert rec1["server_public_key"] != rec2["server_public_key"]
        assert rec1["oprf_seed"] != rec2["oprf_seed"]

    def test_empty_password(self):
        config = OPAQUEConfig(curve="P-256")
        user_id = b"empty-pw-user"
        server_id = b"server"

        record = OPAQUERegistration.register(config, b"", user_id, server_id)
        server = OPAQUEServer(config, record)
        client = OPAQUEClient(config, b"", user_id, server_id)

        msg1 = client.start()
        msg2 = server.start(msg1)
        msg3 = client.finish(msg2)
        sk = server.finish(msg3)

        assert sk == client.get_shared_secret()

    def test_long_password(self):
        config = OPAQUEConfig(curve="P-256")
        pw = b"x" * 500
        user_id = b"longpw-user"
        server_id = b"server"

        record = OPAQUERegistration.register(config, pw, user_id, server_id)
        server = OPAQUEServer(config, record)
        client = OPAQUEClient(config, pw, user_id, server_id)

        msg1 = client.start()
        msg2 = server.start(msg1)
        msg3 = client.finish(msg2)
        sk = server.finish(msg3)

        assert sk == client.get_shared_secret()


class TestOPAQUERejects:
    def test_reject_unknown_curve(self):
        with pytest.raises(PAKEError, match="Unsupported"):
            OPAQUEConfig(curve="secp256k1")

    def test_finish_before_start_client(self):
        config = OPAQUEConfig(curve="P-256")
        client = OPAQUEClient(config, b"pw", b"u", b"s")
        with pytest.raises(PAKEError, match="start"):
            client.finish(b"\x00")

    def test_finish_before_start_server(self):
        config = OPAQUEConfig(curve="P-256")
        record = OPAQUERegistration.register(config, b"pw", b"u", b"s")
        server = OPAQUEServer(config, record)
        with pytest.raises(PAKEError, match="start"):
            server.finish(b"\x00")


class TestOPAQUEConfig:
    def test_default_config(self):
        config = OPAQUEConfig()
        assert config.curve == "P-256"
        assert config.hash_name == "sha256"

    def test_config_curve_upper(self):
        config = OPAQUEConfig(curve="p-256")
        assert config.curve == "P-256"

    def test_config_curve_lower(self):
        config = OPAQUEConfig(curve="Ed25519")
        assert config.curve == "ed25519"

    def test_config_curve_p384(self):
        config = OPAQUEConfig(curve="P-384")
        assert config.curve == "P-384"
        assert config.hash_name == "sha384"

    def test_config_curve_p521(self):
        config = OPAQUEConfig(curve="P-521")
        assert config.curve == "P-521"
        assert config.hash_name == "sha512"
