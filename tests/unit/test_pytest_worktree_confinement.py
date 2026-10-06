"""Regression tests for linked-worktree pytest confinement."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from tests import conftest as pytest_config


def _linked_checkout(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    git_dir = main / ".git" / "worktrees" / "linked"
    git_dir.mkdir(parents=True)
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    return main.resolve(), linked.resolve()


def _guard(main: Path, linked: Path) -> pytest_config._WorktreeConfinement:
    guard_type = pytest_config._WorktreeConfinement
    return guard_type(active_root=linked, canonical_root=main)


def test_linked_worktree_discovers_canonical_main_checkout(tmp_path: Path) -> None:
    """The linked .git marker resolves to the main checkout identity."""
    main, linked = _linked_checkout(tmp_path)

    discover = pytest_config._linked_worktree_main_checkout

    assert discover(linked) == main
    assert discover(main) is None


def test_linked_worktree_discovery_rejects_malformed_markers(tmp_path: Path) -> None:
    """Only the linked-worktree administrative layout identifies main."""
    linked = tmp_path / "linked"
    linked.mkdir()
    marker = linked / ".git"
    discover = pytest_config._linked_worktree_main_checkout

    marker.write_text("not-a-gitdir\n", encoding="utf-8")
    assert discover(linked) is None

    marker.write_text("worktree: /tmp/main/.git/worktrees/linked\n", encoding="utf-8")
    assert discover(linked) is None

    marker.write_text("gitdir: ../main/.git/not-worktrees/linked\n", encoding="utf-8")
    assert discover(linked) is None


def test_linked_worktree_discovery_accepts_relative_gitdir(tmp_path: Path) -> None:
    """A relative gitdir is resolved from the linked checkout marker."""
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    (main / ".git" / "worktrees" / "linked").mkdir(parents=True)
    linked.mkdir()
    (linked / ".git").write_text(
        "gitdir: ../main/.git/worktrees/linked\n",
        encoding="utf-8",
    )

    assert pytest_config._linked_worktree_main_checkout(linked) == main.resolve()


def test_confinement_denies_python_writes_to_canonical_main(tmp_path: Path) -> None:
    """A Python write cannot cross from the linked checkout into main."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)

    with pytest.raises(PermissionError, match="canonical main checkout"):
        guard.audit("open", (main / "Makefile", "w", os.O_CREAT | os.O_WRONLY))

    guard.audit("open", (linked / "Makefile", "w", os.O_CREAT | os.O_WRONLY))
    guard.audit("open", (main / "Makefile", "r", os.O_RDONLY))


def test_confinement_resolves_relative_and_directory_fd_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relative and dir-fd mutations retain their filesystem identity."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)
    monkeypatch.chdir(linked)

    assert guard._resolve_path("child") == linked / "child"
    assert guard._resolve_path(object()) is None
    assert guard._resolve_path("child", -999_999) is None

    directory_fd = os.open(main, os.O_RDONLY)
    try:
        assert guard._resolve_path(directory_fd) == main
        with pytest.raises(PermissionError, match=r"os\.mkdir"):
            guard.audit("os.mkdir", ("child", 0o755, directory_fd))
    finally:
        os.close(directory_fd)


def test_confinement_handles_descriptor_and_resolution_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported descriptors and malformed paths fail without escaping."""
    import fcntl

    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)

    def _unreadable_descriptor(_path: str) -> str:
        raise OSError("unavailable")

    monkeypatch.setattr(os, "readlink", _unreadable_descriptor)
    monkeypatch.delattr(fcntl, "F_GETPATH", raising=False)
    assert guard._dir_fd_path(-1) is None

    monkeypatch.setattr(fcntl, "F_GETPATH", 50, raising=False)
    monkeypatch.setattr(fcntl, "fcntl", lambda *_args: 0)
    assert guard._dir_fd_path(-1) is None
    assert guard._resolve_path("\0") is None

    def _unresolvable(_path: Path) -> Path:
        raise OSError("unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "resolve", _unresolvable)
        assert guard._resolve_path("child") is None


def test_confinement_denies_canonical_main_rename_destination(tmp_path: Path) -> None:
    """Atomic replacement cannot bypass the direct-open boundary."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)

    with pytest.raises(PermissionError, match=r"os\.rename"):
        guard.audit(
            "os.rename",
            (linked / "candidate", main / "CHANGELOG.md", -1, -1),
        )


@pytest.mark.parametrize(
    ("event", "args"),
    [
        ("os.chmod", lambda main: (main / "file", 0o600, -1)),
        ("os.remove", lambda main: (main / "file", -1)),
        ("os.symlink", lambda main: ("target", main / "link", -1)),
        ("os.truncate", lambda main: (main / "file", 0)),
    ],
)
def test_confinement_denies_other_canonical_mutation_events(
    tmp_path: Path,
    event: str,
    args: Callable[[Path], tuple[object, ...]],
) -> None:
    """Every registered write-capable path event uses the same boundary."""
    main, linked = _linked_checkout(tmp_path)

    with pytest.raises(PermissionError, match=event.replace(".", r"\.")):
        _guard(main, linked).audit(event, args(main))


def test_confinement_denies_subprocess_cwd_in_canonical_main(tmp_path: Path) -> None:
    """Nested release tools cannot inherit the canonical checkout as cwd."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)

    with pytest.raises(PermissionError, match=r"subprocess\.Popen"):
        guard.audit(
            "subprocess.Popen",
            ("uv", ("uv", "run", "mypy", "src"), str(main), None),
        )

    guard.audit(
        "subprocess.Popen",
        ("uv", ("uv", "run", "mypy", "src"), str(linked), {}),
    )


def test_confinement_denies_canonical_main_in_subprocess_argv(tmp_path: Path) -> None:
    """A confined cwd cannot hide a canonical-main argument."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)

    with pytest.raises(PermissionError, match="subprocess argv"):
        guard.audit(
            "subprocess.Popen",
            (
                "codex",
                ("codex", "exec", "--cwd", str(main)),
                str(linked),
                {},
            ),
        )


def test_confinement_detects_aliases_and_pathlike_payloads(tmp_path: Path) -> None:
    """Payload scanning resolves bytes, Path objects, aliases, and containers."""
    main, linked = _linked_checkout(tmp_path)
    alias = tmp_path / "checkout-alias"
    alias.symlink_to(main, target_is_directory=True)
    guard = _guard(main, linked)

    assert guard._contains_canonical_reference(os.fsencode(main))
    assert guard._contains_canonical_reference(main)
    assert guard._contains_canonical_reference({"root": alias})
    assert guard._contains_canonical_reference({str(alias)})
    assert guard._contains_canonical_reference(f"ROOT={alias}")
    assert guard._contains_canonical_reference(f"/unrelated{os.pathsep}{alias}")
    assert not guard._contains_canonical_reference(7)
    assert not guard._contains_canonical_reference("unrelated")

    other = tmp_path / "other"
    guard.audit("open", (other / "file", "w", os.O_WRONLY))
    guard.audit("os.link", (linked / "source", linked / "destination", -1, -1))
    guard.audit("unrelated.audit.event", ())


def test_confinement_denies_canonical_main_in_subprocess_env(tmp_path: Path) -> None:
    """Inherited project-root variables cannot redirect a nested agent."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)

    with pytest.raises(PermissionError, match="subprocess environment"):
        guard.audit(
            "subprocess.Popen",
            (
                "codex",
                ("codex", "exec"),
                str(linked),
                {"CLAUDE_PROJECT_DIR": str(main)},
            ),
        )


def test_confinement_denies_canonical_main_in_inherited_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The effective os._Environ mapping cannot redirect a nested agent."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)
    monkeypatch.setenv("GLUDD_TEST_PROJECT_ROOT", str(main))

    with pytest.raises(PermissionError, match="subprocess environment"):
        guard.audit(
            "subprocess.Popen",
            ("codex", ("codex", "exec"), str(linked), None),
        )


@pytest.mark.parametrize(
    "argv",
    [
        ("git", "worktree", "lock", "elsewhere"),
        ("git", "checkout", "-b", "agent/new"),
        ("git", "switch", "-c", "agent/new"),
        ("make", "agent-worktree-base", "BRANCH=agent/new"),
        ("gmake", "feature-start", "MSG=agent/new"),
    ],
)
def test_confinement_classifies_repository_mutation_variants(argv: tuple[str, ...]) -> None:
    """Branch and worktree creation variants fail the same classifier."""
    classify = pytest_config._WorktreeConfinement._is_repository_mutation

    assert classify(argv)
    assert not classify(("git", "worktree", "list"))
    assert not classify(("make", "test-count"))
    assert not classify(object())


def test_confinement_normalizes_supported_subprocess_argv() -> None:
    """Audit payloads are normalized without invoking a shell."""
    normalize = pytest_config._WorktreeConfinement._subprocess_argv

    assert normalize(b"node --version") == ("node", "--version")
    assert normalize("node --version") == ("node", "--version")
    assert normalize("unterminated '") == ("unterminated '",)
    assert normalize(["git", Path("status"), 7]) == ("git", "status")
    assert normalize(object()) == ()


def test_confinement_denies_process_creation_during_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Import-time code cannot launch a child that resolves its own repo."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)
    monkeypatch.setattr(pytest_config, "_PYTEST_COLLECTION_ACTIVE", True)

    with pytest.raises(PermissionError, match="during pytest collection"):
        guard.audit(
            "subprocess.Popen",
            ("codex", ("codex", "exec"), str(linked), {}),
        )


def test_confinement_allows_only_exact_version_probe_during_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collection may query an executable version without running work."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)
    monkeypatch.setattr(pytest_config, "_PYTEST_COLLECTION_ACTIVE", True)

    guard.audit(
        "subprocess.Popen",
        ("node", ("node", "--version"), str(linked), {}),
    )

    with pytest.raises(PermissionError, match="during pytest collection"):
        guard.audit(
            "subprocess.Popen",
            ("node", ("node", "--version", "plugin.ts"), str(linked), {}),
        )


def test_confinement_guards_spawn_and_shell_collection_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alternate process APIs retain the collection and payload boundary."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)
    monkeypatch.setattr(pytest_config, "_PYTEST_COLLECTION_ACTIVE", True)

    guard.audit("os.posix_spawn", ("node", ("node", "--version"), {}))
    guard.audit("os.posix_spawnp", ("node", ("node", "--version"), {}))

    with pytest.raises(PermissionError, match="during pytest collection"):
        guard.audit("os.posix_spawn", ("codex", ("codex", "exec"), {}))
    with pytest.raises(PermissionError, match="during pytest collection"):
        guard.audit("os.system", ("node --version",))
    with pytest.raises(PermissionError, match="subprocess argv"):
        guard.audit(
            "os.posix_spawn",
            ("codex", ("codex", "exec", str(main)), {}),
        )

    monkeypatch.setattr(pytest_config, "_PYTEST_COLLECTION_ACTIVE", False)
    guard.audit("os.system", ("true",))
    guard.audit("os.posix_spawn", ("node", ("node", "script.js"), {}))


def test_confinement_denies_git_worktree_creation_during_test(tmp_path: Path) -> None:
    """A test child cannot create a branch-backed sibling worktree."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)

    with pytest.raises(PermissionError, match="repository mutation subprocess"):
        guard.audit(
            "subprocess.Popen",
            (
                "git",
                (
                    "git",
                    "worktree",
                    "add",
                    str(tmp_path / "escape"),
                    "-b",
                    "agent/codex-release-integrity",
                ),
                str(linked),
                {},
            ),
        )


def test_confinement_allows_git_mutation_in_canonical_pytest_tmp_repo(
    tmp_path: Path,
) -> None:
    """A test may mutate its own canonical, isolated temporary repository."""
    main, linked = _linked_checkout(tmp_path)
    isolated_repo = tmp_path / "isolated-repo"
    (isolated_repo / ".git").mkdir(parents=True)
    guard = _guard(main, linked)

    guard.audit(
        "subprocess.Popen",
        (
            "git",
            ("git", "checkout", "-b", "feature-test"),
            str(isolated_repo),
            {},
        ),
    )


def test_confinement_allows_literal_pytest_basetemp_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI may place its isolated repository below a literal ``pytest`` root."""
    main, linked = _linked_checkout(tmp_path)
    isolated_repo = tmp_path / "ci-basetemp" / "pytest" / "case" / "isolated-repo"
    (isolated_repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    _guard(main, linked).audit(
        "subprocess.Popen",
        (
            "git",
            ("git", "checkout", "-b", "feature-test"),
            str(isolated_repo),
            {},
        ),
    )


def test_confinement_allows_git_dash_c_mutation_in_pytest_tmp_repo(
    tmp_path: Path,
) -> None:
    """An absolute ``git -C`` identifies the isolated repo when cwd is inherited."""
    main, linked = _linked_checkout(tmp_path)
    isolated_repo = tmp_path / "isolated-repo"
    (isolated_repo / ".git").mkdir(parents=True)
    guard = _guard(main, linked)

    guard.audit(
        "subprocess.Popen",
        (
            "git",
            ("git", "-C", str(isolated_repo), "switch", "-c", "feature-test"),
            None,
            {},
        ),
    )

    with pytest.raises(PermissionError, match="repository mutation subprocess"):
        guard.audit(
            "subprocess.Popen",
            (
                "git",
                ("git", "-C", str(linked), "switch", "-c", "feature-test"),
                None,
                {},
            ),
        )


def test_confinement_accepts_trusted_temp_root_alias_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform temp-root alias is safe, but a repository alias is not."""
    main, linked = _linked_checkout(tmp_path)
    physical_temp = tmp_path / "physical-temp"
    physical_temp.mkdir()
    temp_alias = tmp_path / "temp-alias"
    temp_alias.symlink_to(physical_temp, target_is_directory=True)
    isolated_repo = physical_temp / "pytest-case" / "isolated-repo"
    (isolated_repo / ".git").mkdir(parents=True)
    repo_alias = physical_temp / "pytest-case" / "repo-alias"
    repo_alias.symlink_to(isolated_repo, target_is_directory=True)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_alias))
    guard = _guard(main, linked)
    mutation = ("git", "switch", "-c", "feature-test")

    guard.audit(
        "subprocess.Popen",
        ("git", mutation, str(temp_alias / "pytest-case" / "isolated-repo"), {}),
    )
    with pytest.raises(PermissionError, match="repository mutation subprocess"):
        guard.audit("subprocess.Popen", ("git", mutation, str(repo_alias), {}))


def test_confinement_allows_only_registered_temporary_directory_repos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime temp roots are admitted only after their creation audit event."""
    main, linked = _linked_checkout(tmp_path)
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    owned_root = system_temp / "tmp-owned"
    unowned_root = system_temp / "tmp-unowned"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(system_temp))
    monkeypatch.setattr(pytest_config, "_PYTEST_TEMP_ROOT_PREFIXES", ("never-",))
    guard = _guard(main, linked)

    guard.audit("tempfile.mkdtemp", (str(owned_root),))
    (owned_root / "repo" / ".git").mkdir(parents=True)
    (unowned_root / "repo" / ".git").mkdir(parents=True)
    mutation = ("git", "worktree", "add", "target", "-b", "feature-test")

    guard.audit(
        "subprocess.Popen",
        ("git", mutation, str(owned_root / "repo"), {}),
    )
    with pytest.raises(PermissionError, match="repository mutation subprocess"):
        guard.audit(
            "subprocess.Popen",
            ("git", mutation, str(unowned_root / "repo"), {}),
        )


def test_pytest_configure_registers_explicit_basetemp_for_isolated_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact pytest basetemp is trusted without admitting temp siblings."""
    main, linked = _linked_checkout(tmp_path)
    basetemp = tmp_path / "gi-deadbeef-12345"
    isolated_repo = basetemp / "popen-gw0" / "test_pipeline0" / "repo"
    outside_repo = tmp_path / "unowned" / "repo"
    (isolated_repo / ".git").mkdir(parents=True)
    (outside_repo / ".git").mkdir(parents=True)
    guard = _guard(main, linked)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        pytest_config,
        "_WORKTREE_CONFINEMENT",
        guard,
        raising=False,
    )

    pytest_config.pytest_configure(
        cast(pytest.Config, SimpleNamespace(option=SimpleNamespace(basetemp=basetemp)))
    )
    mutation = ("git", "checkout", "-b", "feature-test")
    guard.audit(
        "subprocess.Popen",
        ("git", mutation, str(isolated_repo), {}),
    )
    with pytest.raises(PermissionError, match="repository mutation subprocess"):
        guard.audit(
            "subprocess.Popen",
            ("git", mutation, str(outside_repo), {}),
        )


def test_confinement_allows_only_common_git_lock_metadata(tmp_path: Path) -> None:
    """The linked checkout may coordinate via its exact shared advisory lock."""
    main, linked = _linked_checkout(tmp_path)
    guard = _guard(main, linked)
    advisory_lock = main / ".git" / "gludd-git.lock"

    guard.audit("open", (advisory_lock, None, os.O_CREAT | os.O_RDWR))
    guard.audit("os.utime", (advisory_lock, None, None, -1))

    for unsafe_path in (
        main / ".git" / "index.lock",
        main / ".git" / "gludd-git.lock.extra",
        main / "Makefile",
    ):
        with pytest.raises(PermissionError, match="canonical main checkout"):
            guard.audit("open", (unsafe_path, "w", os.O_CREAT | os.O_WRONLY))


def test_confinement_denies_git_mutation_for_unsafe_repository_cwd(
    tmp_path: Path,
) -> None:
    """Missing, relative, traversing, symlinked, and non-repo cwd fail closed."""
    main, linked = _linked_checkout(tmp_path)
    isolated_repo = tmp_path / "isolated-repo"
    (isolated_repo / ".git").mkdir(parents=True)
    alias = tmp_path / "repo-alias"
    alias.symlink_to(isolated_repo, target_is_directory=True)
    not_repo = tmp_path / "not-repo"
    not_repo.mkdir()
    guard = _guard(main, linked)
    mutation = ("git", "worktree", "add", "target", "-b", "feature-test")

    unsafe_cwds: tuple[str | None, ...] = (
        None,
        isolated_repo.name,
        str(isolated_repo / ".." / isolated_repo.name),
        str(alias),
        str(tmp_path / "missing-repo"),
        str(not_repo),
        str(linked),
    )
    for cwd in unsafe_cwds:
        with pytest.raises(PermissionError, match="repository mutation subprocess"):
            guard.audit("subprocess.Popen", ("git", mutation, cwd, {}))

    with pytest.raises(PermissionError, match="canonical main checkout"):
        guard.audit("subprocess.Popen", ("git", mutation, str(main), {}))


def test_confinement_denies_chdir_into_canonical_main_alias(tmp_path: Path) -> None:
    """A symlink alias cannot make later relative writes escape the worktree."""
    main, linked = _linked_checkout(tmp_path)
    alias = tmp_path / "main-alias"
    alias.symlink_to(main, target_is_directory=True)
    guard = _guard(main, linked)

    with pytest.raises(PermissionError, match=r"os\.chdir"):
        guard.audit("os.chdir", (alias,))

    guard.audit("os.chdir", (linked,))


def test_unowned_gunicorn_audit_hook_is_unit_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adjacent process hook denies only real unit-test Gunicorn execs."""
    audit = pytest_config._deny_unowned_unit_gunicorn

    audit("open", ())
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    audit("subprocess.Popen", ("gunicorn",))

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/unit/test_example.py::test_x (call)")
    audit("subprocess.Popen", (object(),))
    audit("subprocess.Popen", ("python",))
    with pytest.raises(RuntimeError, match="unowned Gunicorn"):
        audit("subprocess.Popen", ("gunicorn",))


def test_linked_session_installs_subprocess_boundary() -> None:
    """The live conftest hook rejects a harmless child rooted in main."""
    active_root = Path(pytest_config._REPO_ROOT)
    main = pytest_config._linked_worktree_main_checkout(active_root)
    if main is None:
        pytest.skip("canonical checkout has no cross-worktree boundary")

    with pytest.raises(PermissionError, match=r"subprocess\.Popen"):
        subprocess.run(
            [sys.executable, "-c", "pass"],
            cwd=main,
            check=False,
        )


def test_live_boundary_skips_without_linked_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canonical checkout has no cross-worktree boundary to install."""
    monkeypatch.setattr(
        pytest_config,
        "_linked_worktree_main_checkout",
        lambda _root: None,
    )

    with pytest.raises(pytest.skip.Exception):
        test_linked_session_installs_subprocess_boundary()
