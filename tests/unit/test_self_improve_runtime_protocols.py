"""Runtime contracts for method-only self-improvement adapter boundaries."""

from __future__ import annotations

import importlib

import pytest

_METHOD_ONLY_ADAPTERS = (
    ("general_ludd.self_improve.apply", "_EventBus", ("publish",)),
    ("general_ludd.self_improve.apply", "_Reloader", ("reload_changed_modules",)),
    ("general_ludd.self_improve.approval", "_TodoStore", ("get_by_id", "transition", "update", "list_by_status")),
    ("general_ludd.self_improve.codex_comparison", "_ChatLocalModel", ("create_chat_completion",)),
    ("general_ludd.self_improve.codex_comparison", "_LlamaGrammarType", ("from_json_schema",)),
    ("general_ludd.self_improve.codex_comparison", "_LocalModel", ("__call__",)),
    ("general_ludd.self_improve.codex_comparison", "_ModelFactory", ("__call__",)),
    ("general_ludd.self_improve.managed_runner", "_AttemptEvaluator", ("__call__",)),
    ("general_ludd.self_improve.managed_runner", "_CandidatePlanner", ("__call__",)),
    ("general_ludd.self_improve.managed_runner", "_FailureLoader", ("__call__",)),
    ("general_ludd.self_improve.managed_runner", "_ModelManagerFactory", ("__call__",)),
    ("general_ludd.self_improve.managed_runner", "_OutcomeAdapterFactory", ("__call__",)),
    ("general_ludd.self_improve.managed_runner", "_OutcomeRecorder", ("__call__",)),
    ("general_ludd.self_improve.managed_runner", "_ProposalGenerator", ("__call__",)),
    ("general_ludd.self_improve.managed_runner", "_Reservation", ("mark_eligible", "mark_failed")),
    ("general_ludd.self_improve.managed_runner", "_SyntaxRepairBuilder", ("__call__",)),
    ("general_ludd.self_improve.model_lifecycle", "_Downloader", ("download_gguf",)),
    (
        "general_ludd.self_improve.promotion",
        "_PromotionRepository",
        ("acquire", "bind_worktree", "complete", "abandon"),
    ),
    ("general_ludd.self_improve.promotion", "_RootRunner", ("run",)),
    ("general_ludd.self_improve.promotion", "_WorktreeRunner", ("run", "run_command")),
    ("general_ludd.self_improve.runtime", "_AttemptEvaluationAdapter", ("__call__",)),
    ("general_ludd.self_improve.runtime", "_CommandRunner", ("run_command",)),
    ("general_ludd.self_improve.runtime", "_MakeRunnerFactory", ("__call__",)),
    ("general_ludd.self_improve.runtime", "_ObservableRunner", ("run_observable",)),
    ("general_ludd.self_improve.runtime", "_RuntimeMakeRunner", ("run_observable", "run_command", "run")),
    ("general_ludd.self_improve.runtime", "_TargetRunner", ("run",)),
)

_DATA_BEARING_PROTOCOLS = (
    ("general_ludd.self_improve.codex_comparison", "_LlamaCppRuntime"),
    ("general_ludd.self_improve.hf_cache_delete", "_CacheInfo"),
    ("general_ludd.self_improve.hf_cache_delete", "_CachedFileInfo"),
    ("general_ludd.self_improve.hf_cache_delete", "_CachedRepoInfo"),
    ("general_ludd.self_improve.hf_cache_delete", "_CachedRevisionInfo"),
    ("general_ludd.self_improve.hf_cache_delete", "_DeleteStrategy"),
    ("general_ludd.self_improve.managed_runner", "ManagedOutcomeAdapter"),
    ("general_ludd.self_improve.managed_runner", "_LeaseManager"),
    ("general_ludd.self_improve.runtime", "_OwnedProcessGroup"),
)


@pytest.mark.parametrize(("module_name", "protocol_name", "members"), _METHOD_ONLY_ADAPTERS)
def test_method_only_adapter_boundary_is_runtime_checkable(
    module_name: str,
    protocol_name: str,
    members: tuple[str, ...],
) -> None:
    """Accept matching adapter shapes and reject objects missing the interface."""
    protocol = getattr(importlib.import_module(module_name), protocol_name)
    adapter_type = type("ShapeCompatibleAdapter", (), {member: lambda *args, **kwargs: None for member in members})

    assert getattr(protocol, "_is_runtime_protocol", False), protocol_name
    assert isinstance(adapter_type(), protocol)
    assert not isinstance(object(), protocol)


@pytest.mark.parametrize(("module_name", "protocol_name"), _DATA_BEARING_PROTOCOLS)
def test_data_bearing_protocol_remains_static_only(module_name: str, protocol_name: str) -> None:
    """Avoid claiming that a shallow runtime check validates typed state."""
    protocol = getattr(importlib.import_module(module_name), protocol_name)

    assert not getattr(protocol, "_is_runtime_protocol", False), protocol_name
