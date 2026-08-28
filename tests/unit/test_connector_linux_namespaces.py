"""Unit tests for LinuxNamespacesSource connector (injected runner pattern).

Uses injected FakeCommandRunner returning FakeRunResult objects matching
the CommandRunner Protocol so no real /proc or subprocess is needed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from general_ludd.connectors.linux_namespaces import (
    _SHELL_METACHARS,
    CAP_NAMES,
    LinuxNamespacesSource,
)

Connector = LinuxNamespacesSource


# -- fake runner / result mimicking the CommandRunner Protocol ---------------


@dataclass
class FakeRunResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeCommandRunner:
    result: FakeRunResult = field(default_factory=FakeRunResult)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str]) -> FakeRunResult:
        self.calls.append(list(argv))
        return self.result


def _runner(rc: int = 0, stdout: str = "", stderr: str = "") -> FakeCommandRunner:
    return FakeCommandRunner(result=FakeRunResult(returncode=rc, stdout=stdout, stderr=stderr))


# ── kind / name ──────────────────────────────────────────────────────────


def test_kind_and_config() -> None:
    src = Connector(config={"name": "ns-prod"})
    assert src.KIND == "metrics"
    assert src.name == "ns-prod"


def test_name_defaults() -> None:
    assert Connector().name == "linux_namespaces"


# ── health ───────────────────────────────────────────────────────────────


def test_health_ok_on_unshare() -> None:
    """On non-Linux, /proc fails; runner returns unshare --help rc=0."""
    src = Connector(runner=_runner())
    health = src.health()
    assert health["ok"] is True
    assert "unshare" in health["detail"]


def test_health_injected_runner_does_not_probe_host_proc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deterministic injected probe must not inherit the host's /proc state."""

    def unexpected_proc_probe(_path: str) -> str:
        raise AssertionError("host /proc must not bypass the injected runner")

    monkeypatch.setattr(
        "general_ludd.connectors.linux_namespaces.os.readlink",
        unexpected_proc_probe,
    )
    health = Connector(runner=_runner()).health()

    assert health["ok"] is True
    assert "unshare" in health["detail"]


def test_health_not_ok_on_missing_proc_and_failed_unshare() -> None:
    src = Connector(runner=_runner(rc=127, stderr="command not found"))
    health = src.health()
    assert health["ok"] is False


def test_health_never_raises() -> None:
    class Boom:
        def __call__(self, argv: Sequence[str]) -> FakeRunResult:
            raise OSError("no binary")

    src = Connector(runner=Boom())
    health = src.health()
    assert health["ok"] is False
    assert "OSError" in health["detail"]


# ── helper ───────────────────────────────────────────────────────────────


def _ns_src(
    name: str = "ns-test",
    rc: int = 0,
    stdout: str = "",
    stderr: str = "",
    runner: FakeCommandRunner | None = None,
) -> tuple[Connector, FakeCommandRunner]:
    if runner is None:
        r = _runner(rc=rc, stdout=stdout, stderr=stderr)
    else:
        r = runner
        r.result = FakeRunResult(returncode=rc, stdout=stdout, stderr=stderr)
    return Connector(config={"name": name}, runner=r), r


# ── query: namespaces (uses /proc reads, not runner) ─────────────────────


def test_query_namespaces() -> None:
    src, _ = _ns_src()
    records = src.query({"target": "namespaces", "pid": "self"})
    # On macOS /proc/self/ns doesn't exist → returns []
    assert isinstance(records, list)


def test_query_cgroups() -> None:
    src, _ = _ns_src()
    records = src.query({"target": "cgroups"})
    assert isinstance(records, list)


def test_query_capabilities() -> None:
    src, _ = _ns_src()
    records = src.query({"target": "capabilities"})
    assert isinstance(records, list)


def test_cgroup_v2() -> None:
    src, _ = _ns_src()
    records = src.query({"target": "cgroup_v2"})
    assert isinstance(records, list)


# ── query: ns_list (uses injected runner) ────────────────────────────────


LSNS_JSON = json.dumps({
    "namespaces": [
        {"type": "mnt", "ns": 4026531837, "pid": 1, "user": "root", "command": "/sbin/init"},
        {"type": "net", "ns": 4026532456, "pid": 42, "user": "nobody", "command": "nginx"},
    ]
})


def test_query_ns_list() -> None:
    src, runner = _ns_src(stdout=LSNS_JSON)
    records = src.query({"target": "ns_list"})
    assert len(records) == 2
    assert runner.calls == [["lsns", "--json"]]

    first = records[0]
    assert first["kind"] == "metrics"
    assert first["source"] == "ns-test"
    # ns field (inode) is used first; type is fallback
    assert first["labels"]["ns_type"] == "4026531837"
    assert first["labels"]["pid"] == "1"


def test_ns_list_nonzero_returns_empty() -> None:
    src, _ = _ns_src(rc=1, stdout=LSNS_JSON)
    assert src.query({"target": "ns_list"}) == []


def test_ns_list_bad_json_returns_empty() -> None:
    src, _ = _ns_src(stdout="not json")
    assert src.query({"target": "ns_list"}) == []


def test_ns_list_empty_stdout_returns_empty() -> None:
    src, _ = _ns_src(stdout="")
    assert src.query({"target": "ns_list"}) == []


def test_runner_exception_does_not_crash_query() -> None:
    class FailingRunner:
        def __call__(self, argv: Sequence[str]) -> FakeRunResult:
            raise RuntimeError("simulated crash")

    src = Connector(config={"name": "ns-test"}, runner=FailingRunner())
    records = src.query({"target": "ns_list"})
    assert records == []


# ── decode_cap_mask ──────────────────────────────────────────────────────


def test_decode_cap_mask() -> None:
    names = Connector._parse_cap_mask("00000000ffffffff")
    assert "CAP_CHOWN" in names
    assert "CAP_DAC_OVERRIDE" in names
    assert "CAP_DAC_READ_SEARCH" in names
    assert "CAP_MAC_OVERRIDE" in names
    assert 31 in CAP_NAMES


def test_decode_cap_mask_partial() -> None:
    names = Connector._parse_cap_mask("0000000000000003")
    assert set(names) == {"CAP_CHOWN", "CAP_DAC_OVERRIDE"}


def test_decode_cap_mask_empty() -> None:
    assert Connector._parse_cap_mask("0000000000000000") == []


def test_decode_cap_mask_invalid_hex() -> None:
    assert Connector._parse_cap_mask("not valid") == []


def test_decode_cap_mask_empty_string() -> None:
    assert Connector._parse_cap_mask("") == []


def test_cap_names_coverage() -> None:
    assert len(CAP_NAMES) >= 40


# ── injection / validation ───────────────────────────────────────────────


def test_resolve_pid_rejects_negative() -> None:
    src, _ = _ns_src()
    with pytest.raises(ValueError):
        src._resolve_pid(-1)


def test_resolve_pid_rejects_shell_meta() -> None:
    src, _ = _ns_src()
    with pytest.raises(ValueError):
        src._resolve_pid("self; id")
    with pytest.raises(ValueError):
        src._resolve_pid("self|nc")


def test_resolve_pid_accepts_self() -> None:
    src, _ = _ns_src()
    assert src._resolve_pid("self") == "self"


def test_resolve_pid_accepts_int_string() -> None:
    src, _ = _ns_src()
    assert src._resolve_pid("1234") == 1234


def test_resolve_pid_rejects_non_integer_string() -> None:
    src, _ = _ns_src()
    with pytest.raises(ValueError):
        src._resolve_pid("abc")


def test_resolve_pid_rejects_non_scalar() -> None:
    src, _ = _ns_src()
    with pytest.raises(ValueError, match="pid must be int"):
        src._resolve_pid([])


def test_run_requires_injected_runner() -> None:
    src = Connector()
    with pytest.raises(RuntimeError, match="no runner injected"):
        src._run(["lsns", "--json"])


@pytest.mark.parametrize(
    "bad_pid",
    [
        "self; id",
        "self|nc",
        "`id`",
    ],
)
def test_query_injection_pid_returns_empty(bad_pid: str) -> None:
    """query() catches ValueError from _resolve_pid and returns []."""
    src, _ = _ns_src()
    records = src.query({"target": "namespaces", "pid": bad_pid})
    assert records == []


@pytest.mark.parametrize(
    "bad_target",
    [
        "cgroups; id",
        "cgroups|nc",
        "`id`",
    ],
)
def test_injection_target_returns_empty(bad_target: str) -> None:
    """Unknown target (with metacharacters) falls through to return []."""
    src, _ = _ns_src()
    records = src.query({"target": bad_target})
    assert records == []


# ── shell metachars regex ────────────────────────────────────────────────


def test_shell_metachars_regex() -> None:
    assert _SHELL_METACHARS.search("hello world") is not None  # space
    assert _SHELL_METACHARS.search("a;b") is not None
    assert _SHELL_METACHARS.search("a|b") is not None
    assert _SHELL_METACHARS.search("a`b") is not None
    assert _SHELL_METACHARS.search("a$b") is not None
    assert _SHELL_METACHARS.search("safe_value") is None


# ── normalized record shape ──────────────────────────────────────────────


REQUIRED_KEYS = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}


def test_query_returns_normalized_shape() -> None:
    src, _runner = _ns_src(stdout=LSNS_JSON)
    records = src.query({"target": "ns_list"})
    assert len(records) >= 1
    for rec in records:
        missing = REQUIRED_KEYS - set(rec.keys())
        assert not missing, f"record missing keys: {missing}"


# ── unsupported target ───────────────────────────────────────────────────


def test_unsupported_target_empty() -> None:
    src, _ = _ns_src()
    assert src.query({"target": "nonexistent"}) == []


# ── edge cases ───────────────────────────────────────────────────────────


def test_query_handles_non_dict_spec() -> None:
    src, _ = _ns_src()
    assert src.query(None) == []  # type: ignore[arg-type]
    assert src.query("not a dict") == []  # type: ignore[arg-type]


def test_query_handles_non_string_target() -> None:
    src, _ = _ns_src()
    assert src.query({"target": 42}) == []


def test_file_helpers_cover_success_and_fail_closed_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "linux-ns"
    root.mkdir()
    payload = root / "payload"
    payload.write_text("value")
    child = root / "child"
    child.mkdir()
    link = root / "link"
    link.symlink_to(payload)

    assert Connector._read_file(str(payload)) == "value"
    assert Connector._read_file(str(root / "missing")) is None
    assert Connector._list_dir(str(root)) == ["child", "link", "payload"]
    assert Connector._list_dir(str(root / "missing")) == []
    assert Connector._readlink(str(link)) == str(payload)
    assert Connector._readlink(str(payload)) is None


def test_proc_parsers_cover_records_and_malformed_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src, _ = _ns_src()
    files = {
        "/proc/7/cgroup": "\nmalformed\n0::/slice\n2:cpu,memory:/legacy\n",
        "/proc/7/status": "ignored\nName:\tworker\nCapEff:\t0000000000000003\n",
        "/sys/fs/cgroup/slice/cgroup.controllers": "cpu memory\n",
        "/sys/fs/cgroup/slice/cgroup.subtree_control": "+cpu\n",
        "/sys/fs/cgroup/slice/cgroup.events": "populated 1\n",
    }
    monkeypatch.setattr(src, "_read_file", lambda path: files.get(path))

    assert src._read_cgroup_info(7) == [
        {"hierarchy": "0", "controllers": "", "cgroup_path": "/slice"},
        {
            "hierarchy": "2",
            "controllers": "cpu,memory",
            "cgroup_path": "/legacy",
        },
    ]
    assert src._read_capabilities(7)["CapEff"]["names"] == [
        "CAP_CHOWN",
        "CAP_DAC_OVERRIDE",
    ]
    assert src._read_cgroup_v2(7) == [
        {
            "cgroup_path": "slice",
            "controllers": ["cpu", "memory"],
            "subtree_control": ["+cpu"],
            "events": "populated 1",
        }
    ]


def test_proc_parsers_fail_closed_when_files_or_v2_entry_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src, _ = _ns_src()
    monkeypatch.setattr(src, "_read_file", lambda _path: None)
    assert src._read_cgroup_info("self") == []
    assert src._read_capabilities("self") == {}
    assert src._read_cgroup_v2("self") == []

    monkeypatch.setattr(src, "_read_file", lambda _path: "2:cpu:/legacy\n")
    assert src._read_cgroup_v2("self") == []


def test_namespace_and_proc_targets_build_normalized_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src, _ = _ns_src()
    monkeypatch.setattr(
        src,
        "_read_ns_links",
        lambda _pid: {"net": "net:[1]", "mnt": "mnt:[2]"},
    )
    monkeypatch.setattr(
        src,
        "_read_cgroup_info",
        lambda _pid: [
            {"hierarchy": "0", "controllers": "", "cgroup_path": "/slice"}
        ],
    )
    monkeypatch.setattr(
        src,
        "_read_capabilities",
        lambda _pid: {"CapEff": {"hex": "3", "names": ["CAP_CHOWN"]}},
    )
    monkeypatch.setattr(
        src,
        "_read_cgroup_v2",
        lambda _pid: [
            {
                "cgroup_path": "slice",
                "controllers": ["cpu"],
                "subtree_control": [],
                "events": "populated 1",
            }
        ],
    )

    assert [record["labels"]["ns_type"] for record in src.query({})] == [
        "mnt",
        "net",
    ]
    assert src.query({"target": "cgroups", "pid": 7})[0]["raw"][
        "cgroup_path"
    ] == "/slice"
    assert src.query({"target": "capabilities", "pid": 7})[0]["labels"][
        "cap_names"
    ] == "CAP_CHOWN"
    assert src.query({"target": "cgroup_v2", "pid": 7})[0]["labels"][
        "controllers"
    ] == "cpu"


def test_empty_proc_targets_and_unexpected_reader_error_return_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src, _ = _ns_src()
    monkeypatch.setattr(src, "_read_ns_links", lambda _pid: {})
    monkeypatch.setattr(src, "_read_capabilities", lambda _pid: {})
    monkeypatch.setattr(src, "_read_cgroup_v2", lambda _pid: [])
    assert src.query({"target": "namespaces"}) == []
    assert src.query({"target": "capabilities"}) == []
    assert src.query({"target": "cgroup_v2"}) == []

    def fail(_pid: int | str) -> dict[str, str]:
        raise OSError("proc raced with exit")

    monkeypatch.setattr(src, "_read_ns_links", fail)
    assert src.query({"target": "namespaces"}) == []


def test_ns_list_accepts_top_level_list_and_skips_non_mapping_entries() -> None:
    src, _ = _ns_src(stdout=json.dumps([None, {"type": "pid", "pid": 9}]))
    records = src.query({"target": "ns_list"})
    assert len(records) == 1
    assert records[0]["labels"] == {"ns_type": "pid", "pid": "9"}


def test_ns_list_rejects_non_list_namespace_payload() -> None:
    src, _ = _ns_src(stdout=json.dumps({"namespaces": "invalid"}))
    assert src.query({"target": "ns_list"}) == []
