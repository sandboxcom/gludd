"""Focused contracts for event-loop runtime identity helpers."""

from types import SimpleNamespace

import pytest


def test_loop_reexports_extracted_runtime_helpers() -> None:
    from general_ludd.event_loop import loop, runtime_helpers

    assert loop._runtime_work_identity is runtime_helpers.runtime_work_identity
    assert loop._runtime_lease_bucket_key is runtime_helpers.runtime_lease_bucket_key
    assert loop._runtime_lease_bucket_keys is runtime_helpers.runtime_lease_bucket_keys
    assert loop._todo_dependency_ids is runtime_helpers.todo_dependency_ids


def test_runtime_helpers_preserve_owned_and_legacy_lease_fences() -> None:
    from general_ludd.event_loop.runtime_helpers import runtime_lease_bucket_keys

    todo = SimpleNamespace(project_id="project-a", todo_id="todo-7", queue="gpu")

    assert runtime_lease_bucket_keys(todo) == (
        "project:project-a:queue:gpu:todo:todo-7",
        "gpu:todo-7",
    )


def test_runtime_helpers_fail_closed_on_identity_and_dependency_mismatch() -> None:
    from general_ludd.event_loop.runtime_helpers import (
        runtime_work_identity,
        todo_dependency_ids,
    )

    with pytest.raises(ValueError, match="project identity"):
        runtime_work_identity(
            SimpleNamespace(project_id="project-a", todo_id="todo-7", queue="gpu"),
            "project-b",
        )
    with pytest.raises(ValueError, match="non-empty strings"):
        todo_dependency_ids(SimpleNamespace(dependencies='["todo-1", ""]'))


def test_runtime_identity_preserves_owned_fallback_and_unowned_namespaces() -> None:
    from general_ludd.event_loop.runtime_helpers import (
        runtime_lease_bucket_key,
        runtime_work_identity,
    )

    fallback_todo = SimpleNamespace(todo_id="todo-8", queue="core")
    identity, owned = runtime_work_identity(fallback_todo, "project-b")
    assert (identity.project_id, identity.todo_id, identity.queue, owned) == (
        "project-b",
        "todo-8",
        "core",
        True,
    )

    unowned_todo = SimpleNamespace(todo_id="todo-9", queue="cpu")
    identity, owned = runtime_work_identity(unowned_todo)
    assert (identity.project_id, owned) == ("default", False)
    assert runtime_lease_bucket_key(unowned_todo) == "unowned:queue:cpu:todo:todo-9"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ()),
        ("", ()),
        ('["todo-1", "todo-1", "todo-2"]', ("todo-1", "todo-2")),
        (("todo-1", "todo-2"), ("todo-1", "todo-2")),
        (object(), ()),
    ],
)
def test_dependency_decoder_preserves_defaults_order_and_compatibility(
    raw: object,
    expected: tuple[str, ...],
) -> None:
    from general_ludd.event_loop.runtime_helpers import todo_dependency_ids

    assert todo_dependency_ids(SimpleNamespace(dependencies=raw)) == expected


@pytest.mark.parametrize("raw", ["not-json", {"todo-1": True}, {"todo-1"}, [7]])
def test_dependency_decoder_rejects_malformed_persisted_values(raw: object) -> None:
    from general_ludd.event_loop.runtime_helpers import todo_dependency_ids

    with pytest.raises(ValueError, match=r"dependencies|identifiers"):
        todo_dependency_ids(SimpleNamespace(dependencies=raw))


def test_lease_key_tuple_deduplicates_a_legacy_equivalent_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.event_loop import runtime_helpers

    todo = SimpleNamespace(todo_id="todo-10", queue="core")
    monkeypatch.setattr(
        runtime_helpers,
        "runtime_lease_bucket_key",
        lambda *_args, **_kwargs: "core:todo-10",
    )

    assert runtime_helpers.runtime_lease_bucket_keys(todo) == ("core:todo-10",)
