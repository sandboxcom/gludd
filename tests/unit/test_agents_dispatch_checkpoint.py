import general_ludd.agents.dispatch_checkpoint as dispatch_checkpoint
from general_ludd.agents.hibernation import AgentEnvironmentSnapshot, DispatchState


def test_checkpoint_survives_restart_and_corrupt_spool_is_fail_safe(tmp_path):
    base_dir = tmp_path / "snapshots"
    key_file = tmp_path / "keys" / "hibernation.key"
    snapshot = AgentEnvironmentSnapshot(
        task_id="todo-1",
        agent_name="release-agent",
        dispatch_state=DispatchState(todo_id="todo-1", prompt_text="run E2E"),
    )
    first_store = dispatch_checkpoint.DurableHibernationStore(base_dir, key_file=key_file)
    manager = dispatch_checkpoint.CheckpointManager(first_store)
    handle = manager.checkpoint(snapshot, phase="mid_tool_loop")

    restarted_store = dispatch_checkpoint.DurableHibernationStore(base_dir, key_file=key_file)
    restored = restarted_store.hydrate(handle)
    assert restored.dispatch_state is not None
    assert restored.dispatch_state.phase_marker == "mid_tool_loop"

    resumed_manager = dispatch_checkpoint.CheckpointManager(restarted_store)
    resumed_manager.write_spool_offset("../../todo-1", offset=42)
    assert resumed_manager.read_spool_offset("../../todo-1") == 42
    resumed_manager.spool_sidecar_path("../../todo-1").write_text("not json")
    assert resumed_manager.read_spool_offset("../../todo-1") is None
    resumed_manager.clear("todo-1")
    assert not first_store._path_for("todo-1").exists()
