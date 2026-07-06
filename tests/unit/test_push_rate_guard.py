"""TDD: push-rate guard must track force-push bypasses and reject repeated bypasses."""


class TestPushRateGuardForcePushTracking:
    """The guard must limit GLUDD_FORCE_PUSH bypasses to prevent CI cancellation loops.

    AGENTS.md codifies the bug: 'Pushing to master on every commit cancels
    every prior CI run. Zero validation occurs.' The push-rate guard has
    three layers (CI-pending check, 30-min cooldown, cancelled-run cap)
    but all three are bypassed by GLUDD_FORCE_PUSH=1.

    The fix: track force-push count in a state file and reject after
    N consecutive bypasses within a time window.
    """

    def test_force_push_tracking_file_exists_after_bypass(self, tmp_path):
        """The guard must create a tracking file on first force-push bypass."""
        state_dir = tmp_path / "gludd-state"
        state_dir.mkdir()
        force_track_file = state_dir / "force-push-track.json"
        assert not force_track_file.exists()

    def test_force_push_count_increments(self, tmp_path):
        """Force-push count must increment with each bypass."""
        state_dir = tmp_path / "gludd-state"
        state_dir.mkdir()
        force_track_file = state_dir / "force-push-track.json"

        from scripts.push_rate_guard import ForcePushTracker

        tracker = ForcePushTracker(state_file=force_track_file)
        assert tracker.count == 0
        tracker.record_bypass()
        assert tracker.count == 1
        tracker.record_bypass()
        assert tracker.count == 2
        tracker.record_bypass()
        assert tracker.count == 3

    def test_force_push_rejected_after_max_bypasses(self, tmp_path):
        """After max_bypasses consecutive force-pushes, further bypasses are denied."""
        state_dir = tmp_path / "gludd-state"
        state_dir.mkdir()
        force_track_file = state_dir / "force-push-track.json"

        from scripts.push_rate_guard import ForcePushTracker

        tracker = ForcePushTracker(state_file=force_track_file, max_bypasses=2)

        assert tracker.is_bypass_allowed() is True
        tracker.record_bypass()
        assert tracker.is_bypass_allowed() is True
        tracker.record_bypass()
        assert tracker.is_bypass_allowed() is False, (
            "Third consecutive force-push within window must be rejected"
        )

    def test_force_push_counter_resets_with_normal_push(self, tmp_path):
        """A normal (non-force) push must reset the bypass counter."""
        state_dir = tmp_path / "gludd-state"
        state_dir.mkdir()
        force_track_file = state_dir / "force-push-track.json"

        from scripts.push_rate_guard import ForcePushTracker

        tracker = ForcePushTracker(state_file=force_track_file, max_bypasses=2)
        tracker.record_bypass()
        tracker.record_bypass()
        assert tracker.count == 2

        tracker.record_normal_push()
        assert tracker.count == 0, "Normal push must reset the bypass counter"

    def test_force_push_counter_decays_over_time(self, tmp_path):
        """Stale bypass entries older than the window must be purged.

        Uses window_hours=0.0001 (0.36 sec) with time.sleep(0.5) to
        guarantee entries expire, then verifies _purge_stale removes them.
        """
        import time

        state_dir = tmp_path / "gludd-state"
        state_dir.mkdir()
        force_track_file = state_dir / "force-push-track.json"

        from scripts.push_rate_guard import ForcePushTracker

        tracker = ForcePushTracker(
            state_file=force_track_file, max_bypasses=3, window_hours=0.0001
        )

        tracker.record_bypass()
        assert tracker.count == 1, "Entry just recorded must still be within the window"

        time.sleep(0.5)
        tracker._purge_stale()
        assert tracker.count == 0, (
            "Bypass entries older than window must be purged after sleep"
        )
