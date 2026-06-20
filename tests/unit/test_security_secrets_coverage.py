"""Branch-coverage tests for security.{sanitize,capability_lattice,ssrf,auth}
and secrets.{manager,cosign,gitsign}.

Targets the lowest-coverage branches: path-confinement (is_path_within /
confine_path_multi / workspace_roots), SSRF literal-host deny (loopback,
169.254 link-local, file:// scheme), capability lattice protected-ext regex +
role dispatch_kinds, secrets manager _PATH_RE + permitted-mount allowlist +
alias resolve, and the cosign/gitsign _scoped_path helpers.

All tests are pure/offline: tmp_path fixtures only, no real network, no real
OpenBao. The hvac client is replaced by a hand-rolled stub.
"""

from __future__ import annotations

import os

import pytest

from general_ludd.security import capability_lattice as cl
from general_ludd.security import sanitize
from general_ludd.security import ssrf
from general_ludd.security.auth import is_safe_fetch_url as auth_is_safe_fetch_url


# ---------------------------------------------------------------------------
# sanitize: path-confinement (confine_path / confine_path_multi /
# is_path_within / workspace_roots)
# ---------------------------------------------------------------------------


class TestConfinePath:
    def test_relative_candidate_confined_to_root(self, tmp_path):
        root = str(tmp_path)
        confined = sanitize.confine_path("sub/file.md", root)
        assert confined == os.path.realpath(os.path.join(root, "sub/file.md"))

    def test_bare_basename_taken_relative_to_root(self, tmp_path):
        root = str(tmp_path)
        confined = sanitize.confine_path("name.md", root)
        assert confined == os.path.realpath(os.path.join(root, "name.md"))

    def test_parent_traversal_escape_rejected(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        assert sanitize.confine_path("../escape.md", str(sub)) is None

    def test_absolute_candidate_outside_root_rejected(self, tmp_path):
        root = str(tmp_path / "jail")
        os.makedirs(root, exist_ok=True)
        # An absolute candidate replaces the root in os.path.join; the
        # descendant check then rejects it.
        assert sanitize.confine_path("/etc/passwd", root) is None

    def test_root_itself_is_within(self, tmp_path):
        root = str(tmp_path)
        assert sanitize.confine_path(".", root) == os.path.realpath(root)

    def test_empty_candidate_returns_none(self, tmp_path):
        assert sanitize.confine_path("", str(tmp_path)) is None

    def test_empty_root_returns_none(self):
        assert sanitize.confine_path("file.md", "") is None

    def test_symlink_escape_defeated(self, tmp_path):
        jail = tmp_path / "jail"
        jail.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("x")
        link = jail / "link"
        link.symlink_to(outside)
        # Resolving the symlink lands outside the jail -> rejected.
        assert sanitize.confine_path("link/secret.txt", str(jail)) is None


class TestConfinePathMulti:
    def test_confined_when_inside_any_root(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        confined = sanitize.confine_path_multi("f.md", [str(a), str(b)])
        assert confined == os.path.realpath(os.path.join(str(a), "f.md"))

    def test_uses_second_root_when_first_misses(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        target = os.path.realpath(os.path.join(str(b), "x.md"))
        # Absolute path inside b is rejected by a but accepted by b.
        confined = sanitize.confine_path_multi(target, [str(a), str(b)])
        assert confined == target

    def test_none_when_in_no_root(self, tmp_path):
        a = tmp_path / "a"
        a.mkdir()
        assert sanitize.confine_path_multi("/nowhere/x", [str(a)]) is None

    def test_empty_roots_list_returns_none(self, tmp_path):
        assert sanitize.confine_path_multi("f.md", []) is None


class TestIsPathWithin:
    def test_true_for_descendant(self, tmp_path):
        assert sanitize.is_path_within("sub/f.md", str(tmp_path)) is True

    def test_false_for_escape(self, tmp_path):
        sub = tmp_path / "s"
        sub.mkdir()
        assert sanitize.is_path_within("../../etc/passwd", str(sub)) is False

    def test_false_for_empty(self, tmp_path):
        assert sanitize.is_path_within("", str(tmp_path)) is False


class TestWorkspaceRoots:
    def test_includes_cwd_and_tempdir(self):
        import tempfile

        roots = sanitize.workspace_roots()
        assert os.path.realpath(os.getcwd()) in roots
        assert os.path.realpath(tempfile.gettempdir()) in roots

    def test_extra_roots_appended_and_resolved(self, tmp_path):
        extra = str(tmp_path)
        roots = sanitize.workspace_roots(extra)
        assert os.path.realpath(extra) in roots

    def test_none_and_empty_extras_dropped(self):
        roots = sanitize.workspace_roots(None, "")
        # Only cwd + tempdir should remain (no None/"" leaking in).
        assert None not in roots
        assert "" not in roots

    def test_dedupes_repeated_roots(self):
        cwd = os.getcwd()
        roots = sanitize.workspace_roots(cwd, cwd)
        assert roots.count(os.path.realpath(cwd)) == 1


# ---------------------------------------------------------------------------
# sanitize: misc sanitizers (fail-closed branches)
# ---------------------------------------------------------------------------


class TestSanitizePath:
    def test_bare_dotdot_segment_rejected(self):
        # The "foo/.." trailing-component case the regex missed (fail-closed).
        assert sanitize.sanitize_path("foo/..") is None

    def test_leading_dotdot_segment_rejected(self):
        assert sanitize.sanitize_path("..") is None

    def test_traversal_regex_rejected(self):
        assert sanitize.sanitize_path("../etc") is None

    def test_absolute_unix_rejected(self):
        assert sanitize.sanitize_path("/etc/passwd") is None

    def test_absolute_windows_rejected(self):
        assert sanitize.sanitize_path("C:\\Windows") is None

    def test_empty_after_strip_returns_none(self):
        assert sanitize.sanitize_path("   ") is None

    def test_dot_slash_prefix_stripped(self):
        assert sanitize.sanitize_path("./ok/file") == "ok/file"

    def test_clean_relative_passes(self):
        assert sanitize.sanitize_path("a/b/c.md") == "a/b/c.md"


class TestSanitizeJobId:
    def test_empty_rejected(self):
        assert sanitize.sanitize_job_id("") is None

    def test_slash_rejected(self):
        assert sanitize.sanitize_job_id("a/b") is None

    def test_backslash_rejected(self):
        assert sanitize.sanitize_job_id("a\\b") is None

    def test_lowercase_rejected_by_pattern(self):
        assert sanitize.sanitize_job_id("abc") is None

    def test_valid_uppercase_id_passes(self):
        assert sanitize.sanitize_job_id("JOB_123-A") == "JOB_123-A"


class TestSanitizeSkillName:
    def test_empty_rejected(self):
        assert sanitize.sanitize_skill_name("") is None

    def test_whitespace_only_rejected(self):
        assert sanitize.sanitize_skill_name("   ") is None

    def test_slash_rejected(self):
        assert sanitize.sanitize_skill_name("a/b") is None

    def test_nul_rejected(self):
        assert sanitize.sanitize_skill_name("a\x00b") is None

    def test_dotdot_rejected(self):
        assert sanitize.sanitize_skill_name("..") is None

    def test_embedded_dotdot_rejected(self):
        assert sanitize.sanitize_skill_name(".hidden..traverse") is None

    def test_bad_charset_rejected(self):
        assert sanitize.sanitize_skill_name("name$bad") is None

    def test_valid_name_passes(self):
        assert sanitize.sanitize_skill_name("My Skill_1.v2") == "My Skill_1.v2"


# ---------------------------------------------------------------------------
# ssrf / sanitize.is_safe_fetch_url / validate_fetch_url
# ---------------------------------------------------------------------------


class TestSsrfHostIsBlocked:
    @pytest.mark.parametrize(
        "host",
        [
            "",
            "localhost",
            "localhost.localdomain",
            "metadata",
            "metadata.google.internal",
            "instance-data",
            "ip6-localhost",
            "127.0.0.1",
            "169.254.169.254",  # link-local + explicit metadata IP
            "100.100.100.200",  # Alibaba metadata
            "10.0.0.5",  # RFC-1918 private
            "192.168.1.1",  # RFC-1918 private
            "::1",  # IPv6 loopback
            "[::1]",  # bracketed IPv6 loopback
            "0.0.0.0",  # unspecified
            "224.0.0.1",  # multicast
        ],
    )
    def test_blocked_hosts(self, host):
        assert ssrf.host_is_blocked(host) is True

    @pytest.mark.parametrize("host", ["example.com", "1.1.1.1", "8.8.8.8"])
    def test_allowed_hosts(self, host):
        assert ssrf.host_is_blocked(host) is False

    def test_whitespace_only_host_blocked(self):
        assert ssrf.host_is_blocked("   ") is True


class TestSsrfIsUrlBlocked:
    def test_https_public_allowed(self):
        assert ssrf.is_url_blocked("https://example.com/x") is False

    def test_http_allowed_by_default_allowlist(self):
        assert ssrf.is_url_blocked("http://example.com") is False

    def test_file_scheme_blocked(self):
        assert ssrf.is_url_blocked("file:///etc/passwd") is True

    def test_scheme_outside_allowlist_blocked(self):
        assert ssrf.is_url_blocked("ftp://example.com") is True

    def test_https_only_allowlist_blocks_http(self):
        assert ssrf.is_url_blocked(
            "http://example.com", scheme_allowlist={"https"}
        ) is True

    def test_missing_host_blocked(self):
        assert ssrf.is_url_blocked("https://") is True

    def test_non_string_blocked(self):
        assert ssrf.is_url_blocked(None) is True  # type: ignore[arg-type]

    def test_empty_blocked(self):
        assert ssrf.is_url_blocked("") is True

    def test_blocked_host_in_url(self):
        assert ssrf.is_url_blocked("https://169.254.169.254/latest") is True


class TestIsSafeFetchUrl:
    def test_sanitize_https_public_safe(self):
        assert sanitize.is_safe_fetch_url("https://example.com/skill") is True

    def test_sanitize_http_unsafe(self):
        assert sanitize.is_safe_fetch_url("http://example.com") is False

    def test_sanitize_file_scheme_unsafe(self):
        assert sanitize.is_safe_fetch_url("file:///etc/passwd") is False

    def test_sanitize_loopback_unsafe(self):
        assert sanitize.is_safe_fetch_url("https://127.0.0.1/x") is False

    def test_sanitize_link_local_metadata_unsafe(self):
        assert sanitize.is_safe_fetch_url("https://169.254.169.254/") is False

    def test_sanitize_empty_unsafe(self):
        assert sanitize.is_safe_fetch_url("") is False

    def test_sanitize_non_string_unsafe(self):
        assert sanitize.is_safe_fetch_url(None) is False  # type: ignore[arg-type]

    def test_auth_mirror_matches_sanitize(self):
        # auth.is_safe_fetch_url shares the primitive; must agree.
        assert auth_is_safe_fetch_url("https://example.com") is True
        assert auth_is_safe_fetch_url("https://127.0.0.1") is False
        assert auth_is_safe_fetch_url("http://example.com") is False


class TestValidateFetchUrl:
    def test_https_public_returns_url(self):
        url = "https://example.com/x"
        assert sanitize.validate_fetch_url(url) == url

    def test_empty_returns_none(self):
        assert sanitize.validate_fetch_url("") is None

    def test_non_https_returns_none(self):
        assert sanitize.validate_fetch_url("http://example.com") is None

    def test_missing_host_returns_none(self):
        assert sanitize.validate_fetch_url("https:///path") is None

    def test_blocked_metadata_host_returns_none(self):
        assert sanitize.validate_fetch_url("https://100.100.100.200/") is None

    def test_localhost_localdomain_returns_none(self):
        assert sanitize.validate_fetch_url("https://localhost.localdomain") is None


class TestIpAddrIsBlockedAlias:
    def test_alias_importable_and_classifies(self):
        import ipaddress

        assert sanitize._ip_is_blocked(ipaddress.ip_address("127.0.0.1")) is True
        assert sanitize._ip_is_blocked(ipaddress.ip_address("1.1.1.1")) is False


# ---------------------------------------------------------------------------
# capability_lattice: protected-path regex, role dispatch, self-mod gates
# ---------------------------------------------------------------------------


class TestIsProtectedPath:
    @pytest.mark.parametrize(
        "path",
        [
            "src/foo/guardrails.py",
            "a/b/capability_policy.py",
            "x/capability_lattice.pyc",  # .py[cod] regex
            "y/policy.pyo",
            "z/permissions.py",
            "repo/.opencode/config.json",  # substring
            "repo/.claude/settings.json",  # substring
            "pkg/module_utils/capability_policy.py",
            "pkg/security/capability_lattice.py",
        ],
    )
    def test_protected_paths(self, path):
        assert cl.is_protected_path(path) is True

    def test_case_insensitive(self):
        assert cl.is_protected_path("X/GUARDRAILS.PY") is True

    def test_backslash_normalised(self):
        assert cl.is_protected_path("repo\\.claude\\x.json") is True

    @pytest.mark.parametrize(
        "path", ["src/foo/handler.py", "lib/util.py", "data/notes.md"]
    )
    def test_unprotected_paths(self, path):
        assert cl.is_protected_path(path) is False


class TestIsCollectionsPath:
    def test_inside_collections(self):
        assert cl.is_collections_path("ns/collections/foo/x.py") is True

    def test_backslash_collections(self):
        assert cl.is_collections_path("ns\\collections\\x.py") is True

    def test_not_collections(self):
        assert cl.is_collections_path("src/foo/x.py") is False

    def test_substring_not_a_component(self):
        # "mycollections" is not the exact component "collections".
        assert cl.is_collections_path("ns/mycollections/x.py") is False


class TestCapabilitiesFor:
    def test_known_role(self):
        caps = cl.capabilities_for("self_improve_agent")
        assert caps.collections_self_modify is True
        assert "collection" in caps.dispatch_kinds

    def test_unknown_role_is_deny_baseline(self):
        caps = cl.capabilities_for("nope")
        assert caps.collections_self_modify is False
        assert caps.dispatch_kinds == frozenset()

    def test_none_role_baseline(self):
        caps = cl.capabilities_for(None)
        assert caps.dispatch_kinds == frozenset()

    def test_empty_role_baseline(self):
        caps = cl.capabilities_for("")
        assert caps.dispatch_kinds == frozenset()

    def test_non_string_role_baseline(self):
        caps = cl.capabilities_for(123)  # type: ignore[arg-type]
        assert caps.dispatch_kinds == frozenset()

    def test_role_stripped(self):
        caps = cl.capabilities_for("  coder  ")
        assert caps.role == "coder"


class TestRoleMayDispatch:
    def test_self_improve_can_dispatch_collection(self):
        assert cl.role_may_dispatch("self_improve_agent", "collection") is True

    def test_coder_cannot_dispatch_collection(self):
        # coder has no "collection" in dispatch_kinds.
        assert cl.role_may_dispatch("coder", "collection") is False

    def test_coder_can_dispatch_role(self):
        assert cl.role_may_dispatch("coder", "role") is True

    def test_operator_lacks_self_modify_so_collection_denied(self):
        # operator HAS "collection" in dispatch_kinds but lacks
        # collections_self_modify -> the BOTH-gate denies it.
        assert cl.role_may_dispatch("operator", "collection") is False
        assert cl.role_may_dispatch("operator", "role") is True

    def test_unknown_role_denied(self):
        assert cl.role_may_dispatch("nope", "role") is False

    def test_report_status_only_skill(self):
        assert cl.role_may_dispatch("report_status", "skill") is True
        assert cl.role_may_dispatch("report_status", "role") is False

    def test_event_loop_excludes_collection(self):
        assert cl.role_may_dispatch("event_loop", "collection") is False
        assert cl.role_may_dispatch("event_loop", "mcp") is True


class TestCheckDispatch:
    def test_allowed_does_not_raise(self):
        cl.check_dispatch("coder", "role")

    def test_denied_raises_capability_error(self):
        with pytest.raises(cl.CapabilityError):
            cl.check_dispatch("coder", "collection")


class TestCheckSelfModification:
    def test_protected_path_raises_regardless_of_role(self):
        with pytest.raises(cl.ProtectedPathError):
            cl.check_self_modification(
                "src/guardrails.py", "self_improve_agent"
            )

    def test_collections_without_capability_raises(self):
        with pytest.raises(cl.CapabilityError):
            cl.check_self_modification("ns/collections/x.py", "coder")

    def test_collections_with_capability_passes(self):
        cl.check_self_modification(
            "ns/collections/x.py", "self_improve_agent"
        )

    def test_ordinary_path_passes(self):
        cl.check_self_modification("src/foo/handler.py", "coder")


# ---------------------------------------------------------------------------
# secrets.manager: _PATH_RE, permitted-mount allowlist, alias resolve
# ---------------------------------------------------------------------------


from general_ludd.secrets.manager import (  # noqa: E402
    SecretAlias,
    SecretsManager,
    SecretsUnavailableError,
)


class _StubKvV2:
    def __init__(self, *, data=None, exc=None):
        self._data = data
        self._exc = exc
        self.calls = []

    def read_secret_version(self, path, mount_point):
        self.calls.append((path, mount_point))
        if self._exc is not None:
            raise self._exc
        return self._data


class _StubKv:
    def __init__(self, v2):
        self.v2 = v2


class _StubSecrets:
    def __init__(self, kv):
        self.kv = kv


class _StubClient:
    def __init__(self, *, data=None, exc=None):
        self.secrets = _StubSecrets(_StubKv(_StubKvV2(data=data, exc=exc)))


class TestRegisterAlias:
    def test_valid_path_and_mount_registers(self):
        mgr = SecretsManager()
        mgr.register_alias(SecretAlias("a", "foo/bar_baz", mount="secret"))
        assert "a" in mgr.list_aliases()

    def test_invalid_path_charset_rejected(self):
        mgr = SecretsManager()
        with pytest.raises(ValueError):
            mgr.register_alias(SecretAlias("a", "bad path!", mount="secret"))

    def test_dotdot_segment_rejected(self):
        mgr = SecretsManager()
        with pytest.raises(ValueError):
            mgr.register_alias(SecretAlias("a", "foo/../bar", mount="secret"))

    def test_path_starting_with_dash_rejected(self):
        # _PATH_RE requires the first char to be [A-Za-z0-9_].
        mgr = SecretsManager()
        with pytest.raises(ValueError):
            mgr.register_alias(SecretAlias("a", "-leading", mount="secret"))

    def test_disallowed_mount_rejected(self):
        mgr = SecretsManager()
        with pytest.raises(ValueError):
            mgr.register_alias(SecretAlias("a", "ok", mount="forbidden"))

    def test_kv_mount_allowed(self):
        mgr = SecretsManager()
        mgr.register_alias(SecretAlias("a", "ok", mount="kv"))
        assert "a" in mgr.list_aliases()


class TestResolveAlias:
    def test_unknown_alias_returns_none(self):
        mgr = SecretsManager()
        assert mgr.resolve("missing") is None

    def test_no_client_returns_none(self):
        mgr = SecretsManager(aliases={"a": SecretAlias("a", "p")})
        assert mgr.resolve("a") is None

    def test_resolves_value_from_client(self):
        client = _StubClient(data={"data": {"data": {"value": "s3cr3t"}}})
        mgr = SecretsManager(
            client=client, aliases={"a": SecretAlias("a", "p", mount="secret")}
        )
        assert mgr.resolve("a") == "s3cr3t"

    def test_missing_value_key_returns_empty_string(self):
        client = _StubClient(data={"data": {"data": {}}})
        mgr = SecretsManager(
            client=client, aliases={"a": SecretAlias("a", "p")}
        )
        assert mgr.resolve("a") == ""

    def test_genuine_not_found_returns_none(self):
        import hvac

        client = _StubClient(exc=hvac.exceptions.InvalidPath("404"))
        mgr = SecretsManager(
            client=client, aliases={"a": SecretAlias("a", "p")}
        )
        assert mgr.resolve("a") is None

    def test_outage_fails_closed_raises(self):
        client = _StubClient(exc=RuntimeError("vault down"))
        mgr = SecretsManager(
            client=client, aliases={"a": SecretAlias("a", "p")}
        )
        with pytest.raises(SecretsUnavailableError):
            mgr.resolve("a")

    def test_unexpected_shape_returns_none(self):
        client = _StubClient(data={"unexpected": True})
        mgr = SecretsManager(
            client=client, aliases={"a": SecretAlias("a", "p")}
        )
        assert mgr.resolve("a") is None


class TestReadSecretGuard:
    def test_not_connected_raises_unavailable(self):
        mgr = SecretsManager()
        with pytest.raises(SecretsUnavailableError):
            mgr.read_secret("some/path")


# ---------------------------------------------------------------------------
# secrets.cosign / gitsign: _scoped_path + read/write round-trips
# ---------------------------------------------------------------------------


from general_ludd.secrets import cosign as cosign_mod  # noqa: E402
from general_ludd.secrets import gitsign as gitsign_mod  # noqa: E402


class _RecordingMgr:
    """Captures write_secret / read_secret calls for the cosign/gitsign tests."""

    def __init__(self, read_value=None):
        self.written = {}
        self._read_value = read_value

    def write_secret(self, path, value):
        self.written[path] = value

    def read_secret(self, path):
        if isinstance(self._read_value, dict):
            return self._read_value
        return self._read_value


class TestCosignScopedPath:
    def test_scoped_path_format(self):
        assert (
            cosign_mod._scoped_path("proj1", "release")
            == "projects/proj1/cosign/release"
        )

    def test_write_uses_scoped_path(self):
        mgr = _RecordingMgr()
        cosign_mod.write_cosign_key(
            mgr, "proj1", "release", "PRIV", "PUB", password="pw"
        )
        assert "projects/proj1/cosign/release" in mgr.written
        payload = mgr.written["projects/proj1/cosign/release"]
        assert payload["private_key"] == "PRIV"
        assert payload["public_key"] == "PUB"
        assert payload["password"] == "pw"
        assert "created_at" in payload

    def test_read_none_returns_none(self):
        mgr = _RecordingMgr(read_value=None)
        assert cosign_mod.read_cosign_key(mgr, "p", "k") is None

    def test_read_round_trip(self):
        mgr = _RecordingMgr(
            read_value={
                "key_name": "k",
                "private_key": "PRIV",
                "public_key": "PUB",
                "password": "pw",
                "created_at": "2026-01-01T00:00:00",
            }
        )
        key = cosign_mod.read_cosign_key(mgr, "p", "k")
        assert key is not None
        assert key.key_name == "k"
        assert key.private_key == "PRIV"
        assert key.created_at == "2026-01-01T00:00:00"

    def test_read_defaults_for_missing_fields(self):
        mgr = _RecordingMgr(read_value={})
        key = cosign_mod.read_cosign_key(mgr, "p", "kdefault")
        assert key is not None
        assert key.key_name == "kdefault"  # falls back to passed key_name
        assert key.private_key == ""
        assert key.password is None

    def test_delete_uses_scoped_path(self):
        deleted = {}

        class M:
            def delete_secret(self, path):
                deleted["path"] = path

        cosign_mod.delete_cosign_key(M(), "p", "k")
        assert deleted["path"] == "projects/p/cosign/k"


class TestGitsignScopedPath:
    def test_scoped_path_format(self):
        assert (
            gitsign_mod._scoped_path("proj9")
            == "projects/proj9/gitsign/config"
        )

    def test_write_uses_scoped_path_and_defaults(self):
        mgr = _RecordingMgr()
        gitsign_mod.write_gitsign_config(mgr, "proj9")
        path = "projects/proj9/gitsign/config"
        assert path in mgr.written
        payload = mgr.written[path]
        assert payload["fulcio_url"] == "https://fulcio.sigstore.dev"
        assert payload["enabled"] is True

    def test_write_custom_values(self):
        mgr = _RecordingMgr()
        gitsign_mod.write_gitsign_config(
            mgr,
            "p",
            fulcio_url="https://f.example",
            rekor_url="https://r.example",
            oidc_issuer="https://o.example",
            key_ref="kr",
            enabled=False,
        )
        payload = mgr.written["projects/p/gitsign/config"]
        assert payload["fulcio_url"] == "https://f.example"
        assert payload["key_ref"] == "kr"
        assert payload["enabled"] is False

    def test_read_none_returns_none(self):
        mgr = _RecordingMgr(read_value=None)
        assert gitsign_mod.read_gitsign_config(mgr, "p") is None

    def test_read_round_trip(self):
        mgr = _RecordingMgr(
            read_value={
                "fulcio_url": "https://f",
                "rekor_url": "https://r",
                "oidc_issuer": "https://o",
                "key_ref": "kr",
                "enabled": True,
            }
        )
        cfg = gitsign_mod.read_gitsign_config(mgr, "p")
        assert cfg is not None
        assert cfg.fulcio_url == "https://f"
        assert cfg.key_ref == "kr"
        assert cfg.enabled is True

    def test_read_defaults_for_missing_fields(self):
        mgr = _RecordingMgr(read_value={})
        cfg = gitsign_mod.read_gitsign_config(mgr, "p")
        assert cfg is not None
        assert cfg.fulcio_url == "https://fulcio.sigstore.dev"
        assert cfg.enabled is False
