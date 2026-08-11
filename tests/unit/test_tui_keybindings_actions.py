"""Deep tests for untested TUIKeyHandler action methods.

Covers: _toggle_verbose, _health_refresh, _selftest_run, _loglevel_cycle,
_models_discover, _worktree_scan, _integrity_report, _ansible_builtins,
_filestore_binaries, _filestore_bootstrap, _discovered_refresh,
_handle_code_graph_input, _submit_* helper validation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.tui.keybindings import TUIKeyHandler


def _state(**overrides):
    base: dict = {
        "current_view": "main",
        "daemon_url": "http://127.0.0.1:8000",
        "status_msg": "",
        "dispatch_mode": "active",
        "verbose_logging": False,
        "current_log_level": "info",
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
        "projects_data": [],
        "hooks_data": [],
        "models_data": [],
        "todos_data": [],
        "workers_data": [],
        "agents_data": [],
        "integrity_changes": [],
        "selected_main_idx": 0,
        "selected_project_idx": 0,
        "selected_hook_idx": 0,
        "selected_model_idx": 0,
        "selected_todo_idx": 0,
        "selected_worker_idx": 0,
        "selected_agent_idx": 0,
        "selected_integrity_idx": 0,
        "panel_focus": "left",
        "health_data": {},
        "selftest_data": {},
        "discovered_data": [],
        "code_graph_data": {},
        "ansible_builtins": [],
        "filestore_binaries": [],
    }
    base.update(overrides)
    return base


# ── _toggle_verbose ───────────────────────────────────────────────────────


class TestToggleVerbose:
    def test_turns_on_from_off(self):
        s = _state(verbose_logging=False)
        TUIKeyHandler(s)._toggle_verbose()
        assert s["verbose_logging"] is True
        assert "ON" in s["status_msg"]

    def test_turns_off_from_on(self):
        s = _state(verbose_logging=True)
        TUIKeyHandler(s)._toggle_verbose()
        assert s["verbose_logging"] is False
        assert "OFF" in s["status_msg"]

    def test_defaults_to_false_when_key_missing(self):
        s = _state()
        del s["verbose_logging"]
        TUIKeyHandler(s)._toggle_verbose()
        assert s["verbose_logging"] is True


# ── _health_refresh ──────────────────────────────────────────────────────


class TestHealthRefresh:
    def test_success_stores_data_and_status(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"status": "healthy", "uptime": 3600}
        with patch("httpx.get", return_value=mock_resp):
            TUIKeyHandler(s)._health_refresh()
        assert s["health_data"] == {"status": "healthy", "uptime": 3600}
        assert "healthy" in s["status_msg"]

    def test_non_200_reports_failure(self):
        s = _state()
        mock_resp = MagicMock(status_code=500)
        with patch("httpx.get", return_value=mock_resp):
            TUIKeyHandler(s)._health_refresh()
        assert "failed" in s["status_msg"]

    def test_exception_reports_error(self):
        s = _state()
        with patch("httpx.get", side_effect=Exception("timeout")):
            TUIKeyHandler(s)._health_refresh()
        assert "error" in s["status_msg"].lower() or "timeout" in s["status_msg"]


# ── _selftest_run ────────────────────────────────────────────────────────


class TestSelftestRun:
    def test_success_stores_data_and_counts(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"scenarios_passed": 8, "scenarios_run": 10}
        with patch("httpx.post", return_value=mock_resp):
            TUIKeyHandler(s)._selftest_run()
        assert s["selftest_data"]["scenarios_passed"] == 8
        assert "8/10" in s["status_msg"]

    def test_non_200_reports_failure(self):
        s = _state()
        mock_resp = MagicMock(status_code=500)
        with patch("httpx.post", return_value=mock_resp):
            TUIKeyHandler(s)._selftest_run()
        assert "failed" in s["status_msg"]

    def test_exception_reports_error(self):
        s = _state()
        with patch("httpx.post", side_effect=Exception("crash")):
            TUIKeyHandler(s)._selftest_run()
        assert "error" in s["status_msg"].lower() or "crash" in s["status_msg"]


# ── _loglevel_cycle ──────────────────────────────────────────────────────


class TestLoglevelCycle:
    def test_cycles_from_info_to_warning(self):
        s = _state(current_log_level="info")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {}
        with patch("httpx.post", return_value=mock_resp):
            TUIKeyHandler(s)._loglevel_cycle()
        assert s["current_log_level"] == "warning"
        assert s["last_loglevel"] is True
        assert "warning" in s["status_msg"]

    def test_wraps_from_error_to_debug(self):
        s = _state(current_log_level="error")
        with patch("httpx.post", return_value=MagicMock(status_code=200, json=lambda: {})):
            TUIKeyHandler(s)._loglevel_cycle()
        assert s["current_log_level"] == "debug"

    def test_unknown_current_defaults_to_debug(self):
        s = _state(current_log_level="invalid")
        with patch("httpx.post", return_value=MagicMock(status_code=200, json=lambda: {})):
            TUIKeyHandler(s)._loglevel_cycle()
        assert s["current_log_level"] == "debug"

    def test_non_200_preserves_current_level(self):
        s = _state(current_log_level="info")
        with patch("httpx.post", return_value=MagicMock(status_code=500)):
            TUIKeyHandler(s)._loglevel_cycle()
        assert s["current_log_level"] == "info"
        assert "failed" in s["status_msg"]

    def test_exception_preserves_current_level(self):
        s = _state(current_log_level="info")
        with patch("httpx.post", side_effect=Exception("network")):
            TUIKeyHandler(s)._loglevel_cycle()
        assert s["current_log_level"] == "info"
        assert "error" in s["status_msg"].lower() or "network" in s["status_msg"]


# ── _models_discover ──────────────────────────────────────────────────────


class TestModelsDiscover:
    def test_success_sets_status_with_count(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"discovered_count": 5, "models": []}
        with patch("httpx.post", return_value=mock_resp):
            TUIKeyHandler(s)._models_discover()
        assert "5" in s["status_msg"]
        assert s["last_discover"] is True

    def test_non_200_reports_failure(self):
        s = _state()
        with patch("httpx.post", return_value=MagicMock(status_code=500)):
            TUIKeyHandler(s)._models_discover()
        assert "failed" in s["status_msg"]

    def test_exception_sets_last_discover_and_error(self):
        s = _state()
        with patch("httpx.post", side_effect=Exception("boom")):
            TUIKeyHandler(s)._models_discover()
        assert s["last_discover"] is True
        assert "error" in s["status_msg"].lower() or "boom" in s["status_msg"]


# ── _worktree_scan ────────────────────────────────────────────────────────


class TestWorktreeScan:
    def test_success_shows_tracked_and_abandoned(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"tracked_count": 3, "todos": [{}, {}, {}, {}]}
        with patch("httpx.post", return_value=mock_resp):
            TUIKeyHandler(s)._worktree_scan()
        assert "3 tracked" in s["status_msg"]
        assert "4 abandoned" in s["status_msg"]
        assert s["last_scan"] is True

    def test_zero_abandoned_works(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"tracked_count": 0, "todos": []}
        with patch("httpx.post", return_value=mock_resp):
            TUIKeyHandler(s)._worktree_scan()
        assert "0 tracked" in s["status_msg"]
        assert "0 abandoned" in s["status_msg"]

    def test_non_200_reports_failure(self):
        s = _state()
        with patch("httpx.post", return_value=MagicMock(status_code=500)):
            TUIKeyHandler(s)._worktree_scan()
        assert "failed" in s["status_msg"]

    def test_exception_sets_last_scan_and_error(self):
        s = _state()
        with patch("httpx.post", side_effect=Exception("dead")):
            TUIKeyHandler(s)._worktree_scan()
        assert s["last_scan"] is True
        assert "error" in s["status_msg"].lower() or "dead" in s["status_msg"]


# ── _integrity_report ─────────────────────────────────────────────────────


class TestIntegrityReport:
    def test_success_stores_report(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"scanned": 12, "violations": 2}
        with patch("httpx.get", return_value=mock_resp):
            TUIKeyHandler(s)._integrity_report()
        assert s["integrity_report"]["scanned"] == 12
        assert s["integrity_report"]["violations"] == 2
        assert "loaded" in s["status_msg"].lower()
        assert s["last_report"] is True

    def test_non_200_reports_failure(self):
        s = _state()
        with patch("httpx.get", return_value=MagicMock(status_code=500)):
            TUIKeyHandler(s)._integrity_report()
        assert "failed" in s["status_msg"]

    def test_exception_sets_last_report_and_error(self):
        s = _state()
        with patch("httpx.get", side_effect=Exception("gone")):
            TUIKeyHandler(s)._integrity_report()
        assert s["last_report"] is True
        assert "error" in s["status_msg"].lower() or "gone" in s["status_msg"]


# ── _ansible_builtins ─────────────────────────────────────────────────────


class TestAnsibleBuiltins:
    def test_success_via_daemon(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"modules": ["file", "copy", "template"]}
        with patch("httpx.get", return_value=mock_resp):
            TUIKeyHandler(s)._ansible_builtins()
        assert len(s["ansible_builtins"]) == 3
        assert "3 modules" in s["status_msg"]

    def test_falls_back_to_local_when_daemon_fails(self):
        s = _state()
        with (
            patch("httpx.get", side_effect=Exception("down")),
            patch(
                "general_ludd.ansible.galaxy.get_builtin_modules",
                return_value=["file", "template"],
            ),
        ):
            TUIKeyHandler(s)._ansible_builtins()
        assert len(s["ansible_builtins"]) == 2
        assert "2 modules" in s["status_msg"]

    def test_local_fallback_also_fails(self):
        s = _state()
        with (
            patch("httpx.get", side_effect=Exception("down")),
            patch(
                "general_ludd.ansible.galaxy.get_builtin_modules",
                side_effect=Exception("no galaxy"),
            ),
        ):
            TUIKeyHandler(s)._ansible_builtins()
        assert "error" in s["status_msg"].lower() or "no galaxy" in s["status_msg"]

    def test_non_200_reports_failure_no_fallback(self):
        s = _state()
        mock_resp = MagicMock(status_code=500)
        with patch("httpx.get", return_value=mock_resp):
            TUIKeyHandler(s)._ansible_builtins()
        assert "failed" in s["status_msg"]


# ── _filestore_binaries ───────────────────────────────────────────────────


class TestFilestoreBinaries:
    def test_success_with_count(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"binaries": ["a", "b"], "count": 2}
        with patch("httpx.get", return_value=mock_resp):
            TUIKeyHandler(s)._filestore_binaries()
        assert s["filestore_binaries"] == ["a", "b"]
        assert "2" in s["status_msg"]

    def test_success_without_count_uses_len(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"binaries": ["x", "y", "z"]}
        with patch("httpx.get", return_value=mock_resp):
            TUIKeyHandler(s)._filestore_binaries()
        assert s["filestore_binaries"] == ["x", "y", "z"]
        assert "3" in s["status_msg"]

    def test_non_200_reports_failure(self):
        s = _state()
        with patch("httpx.get", return_value=MagicMock(status_code=500)):
            TUIKeyHandler(s)._filestore_binaries()
        assert "failed" in s["status_msg"]

    def test_exception_reports_error(self):
        s = _state()
        with patch("httpx.get", side_effect=Exception("dead")):
            TUIKeyHandler(s)._filestore_binaries()
        assert "error" in s["status_msg"].lower() or "dead" in s["status_msg"]


# ── _filestore_bootstrap ──────────────────────────────────────────────────


class TestFilestoreBootstrap:
    def test_success_sets_bootstrap_flag(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"binary": "openbao", "status": "installed"}
        with patch("httpx.post", return_value=mock_resp):
            TUIKeyHandler(s)._filestore_bootstrap()
        assert "openbao" in s["status_msg"]
        assert s["last_bootstrap"] is True

    def test_non_200_reports_failure(self):
        s = _state()
        with patch("httpx.post", return_value=MagicMock(status_code=500)):
            TUIKeyHandler(s)._filestore_bootstrap()
        assert "failed" in s["status_msg"]

    def test_exception_sets_last_bootstrap_and_error(self):
        s = _state()
        with patch("httpx.post", side_effect=Exception("dead")):
            TUIKeyHandler(s)._filestore_bootstrap()
        assert s["last_bootstrap"] is True
        assert "error" in s["status_msg"].lower() or "dead" in s["status_msg"]


# ── _discovered_refresh ───────────────────────────────────────────────────


class TestDiscoveredRefresh:
    def test_success_stores_profiles(self):
        s = _state()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"profiles": [{"name": "p1"}, {"name": "p2"}]}
        with patch("httpx.get", return_value=mock_resp):
            TUIKeyHandler(s)._discovered_refresh()
        assert len(s["discovered_data"]) == 2
        assert "profile" in s["status_msg"].lower()

    def test_non_200_reports_failure(self):
        s = _state()
        with patch("httpx.get", return_value=MagicMock(status_code=500)):
            TUIKeyHandler(s)._discovered_refresh()
        assert "failed" in s["status_msg"]

    def test_exception_reports_error(self):
        s = _state()
        with patch("httpx.get", side_effect=Exception("down")):
            TUIKeyHandler(s)._discovered_refresh()
        assert "error" in s["status_msg"].lower() or "down" in s["status_msg"]


# ── _handle_code_graph_input ──────────────────────────────────────────────


class TestHandleCodeGraphInput:
    def test_escape_cancels(self):
        s = _state(input_mode="code_graph", input_buffer="foo.py")
        result = TUIKeyHandler(s)._handle_code_graph_input("\x1b")
        assert result is True
        assert s["input_mode"] is None

    def test_backspace_removes_char(self):
        s = _state(input_mode="code_graph", input_buffer="abc")
        result = TUIKeyHandler(s)._handle_code_graph_input("\x7f")
        assert result is True
        assert s["input_buffer"] == "ab"

    def test_regular_char_appends_to_buffer(self):
        s = _state(input_mode="code_graph", input_buffer="a")
        result = TUIKeyHandler(s)._handle_code_graph_input("b")
        assert result is True
        assert s["input_buffer"] == "ab"

    def test_enter_submits_and_clears_input(self):
        s = _state(input_mode="code_graph", input_buffer="server.py")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"nodes": [{"id": "n1"}, {"id": "n2"}]}
        with patch("httpx.get", return_value=mock_resp):
            result = TUIKeyHandler(s)._handle_code_graph_input("\r")
        assert result is True
        assert s["input_mode"] is None
        assert s["input_buffer"] == ""
        assert s["code_graph_data"]["nodes"][1]["id"] == "n2"
        assert "2 nodes" in s["status_msg"]

    def test_enter_with_failed_request(self):
        s = _state(input_mode="code_graph", input_buffer="bad.py")
        with patch("httpx.get", return_value=MagicMock(status_code=500)):
            result = TUIKeyHandler(s)._handle_code_graph_input("\r")
        assert result is True
        assert s["input_mode"] is None
        assert "failed" in s["status_msg"]

    def test_enter_with_exception(self):
        s = _state(input_mode="code_graph", input_buffer="x.py")
        with patch("httpx.get", side_effect=Exception("timeout")):
            result = TUIKeyHandler(s)._handle_code_graph_input("\r")
        assert result is True
        assert s["input_mode"] is None
        assert "error" in s["status_msg"].lower() or "timeout" in s["status_msg"]


# ── _handle_text_search_input — full search lifecycle ──────────────────────


class TestHandleTextSearchInput:
    def test_escape_cancels_search(self):
        h = TUIKeyHandler(_state(input_mode="mcp_search", input_buffer="test"))
        result = h._handle_text_search_input("\x1b", "mcp_search", "/admin/mcp/search", "mcp_search_results")
        assert result is True
        assert h._state["input_mode"] is None
        assert "Cancelled" in h._state["status_msg"]

    def test_backspace_removes_char(self):
        h = TUIKeyHandler(_state(input_mode="mcp_search", input_buffer="abc"))
        result = h._handle_text_search_input("\x7f", "mcp_search", "/x", "results")
        assert result is True
        assert h._state["input_buffer"] == "ab"

    def test_regular_char_returns_true(self):
        h = TUIKeyHandler(_state(input_mode="mcp_search", input_buffer=""))
        result = h._handle_text_search_input("x", "mcp_search", "/x", "results")
        assert result is True
        assert h._state["input_buffer"] == "x"

    def test_enter_submits_search_and_stores_results(self):
        s = _state(input_mode="mcp_search", input_buffer="test-query")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"results": [{"name": "s1"}]}
        with patch("httpx.get", return_value=mock_resp):
            h = TUIKeyHandler(s)
            result = h._handle_text_search_input("\r", "mcp_search", "/admin/mcp/search", "mcp_search_results")
        assert result is True
        assert s["input_mode"] is None
        assert s["input_buffer"] == ""
        assert "found" in s["status_msg"].lower() or "results" in s["status_msg"].lower()
        assert len(s["mcp_search_results"]) == 1

    def test_enter_with_failed_request(self):
        s = _state(input_mode="mcp_search", input_buffer="q")
        mock_resp = MagicMock(status_code=500)
        with patch("httpx.get", return_value=mock_resp):
            h = TUIKeyHandler(s)
            result = h._handle_text_search_input("\r", "mcp_search", "/admin/mcp/search", "mcp_search_results")
        assert result is True
        assert s["input_mode"] is None
        assert "failed" in s["status_msg"]


# ── handle_key — view-specific action dispatch (non-input-mode) ────────────


class TestHandleKeyViewSpecificActions:
    def test_hooks_view_r_activates_hooks_register(self):
        s = _state(current_view="hooks", input_mode=None)
        TUIKeyHandler(s).handle_key("r")
        assert s["input_mode"] == "hooks_register"
        assert s["input_field_index"] == 0
        assert len(s["input_fields"]) == 2

    def test_hooks_view_d_deletes_hook(self):
        s = _state(current_view="hooks", hooks_data=[{"hook_id": "h1"}, {"hook_id": "h2"}], selected_hook_idx=1)
        MagicMock(status_code=200)
        with patch.object(TUIKeyHandler, "_delete_selected_hook") as mock_del:
            TUIKeyHandler(s).handle_key("d")
            mock_del.assert_called_once()

    def test_integrity_view_s_scans(self):
        s = _state(current_view="integrity")
        with patch.object(TUIKeyHandler, "_integrity_scan") as mock_scan:
            TUIKeyHandler(s).handle_key("s")
            mock_scan.assert_called_once()

    def test_integrity_view_a_approves(self):
        s = _state(current_view="integrity")
        with patch.object(TUIKeyHandler, "_integrity_approve") as mock_app:
            TUIKeyHandler(s).handle_key("a")
            mock_app.assert_called_once()

    def test_integrity_view_r_rejects(self):
        s = _state(current_view="integrity")
        with patch.object(TUIKeyHandler, "_integrity_reject") as mock_rej:
            TUIKeyHandler(s).handle_key("r")
            mock_rej.assert_called_once()

    def test_models_view_x_removes(self):
        s = _state(current_view="models")
        with patch.object(TUIKeyHandler, "_remove_selected_model") as mock_rem:
            TUIKeyHandler(s).handle_key("x")
            mock_rem.assert_called_once()

    def test_ansible_view_i_installs(self):
        s = _state(current_view="ansible")
        TUIKeyHandler(s).handle_key("i")
        assert s["input_mode"] == "ansible_install"

    def test_skills_view_i_installs(self):
        s = _state(current_view="skills")
        TUIKeyHandler(s).handle_key("i")
        assert s["input_mode"] == "skills_install"

    def test_workers_view_p_pings(self):
        s = _state(current_view="workers")
        with patch.object(TUIKeyHandler, "_ping_workers") as mock_ping:
            TUIKeyHandler(s).handle_key("p")
            mock_ping.assert_called_once()

    def test_todos_view_a_activates_add_mode(self):
        s = _state(current_view="todos")
        TUIKeyHandler(s).handle_key("a")
        assert s["input_mode"] == "todos_add"
        assert len(s["input_fields"]) == 2

    def test_projects_view_w_sets_weight_mode(self):
        s = _state(current_view="projects", projects_data=[{"name": "p1", "project_id": "p1"}], selected_project_idx=0)
        TUIKeyHandler(s).handle_key("w")
        assert s["input_mode"] == "projects_set_weight"

    def test_models_view_s_activates_search(self):
        s = _state(current_view="models")
        TUIKeyHandler(s).handle_key("s")
        assert s["input_mode"] == "models_search"

    def test_mcp_view_s_activates_search(self):
        s = _state(current_view="mcp")
        TUIKeyHandler(s).handle_key("s")
        assert s["input_mode"] == "mcp_search"

    def test_skills_view_s_activates_search(self):
        s = _state(current_view="skills")
        TUIKeyHandler(s).handle_key("s")
        assert s["input_mode"] == "skills_search"

    def test_compute_view_a_activates_register(self):
        s = _state(current_view="compute")
        TUIKeyHandler(s).handle_key("a")
        assert s["input_mode"] == "compute_register"

    def test_templates_view_r_refreshes(self):
        s = _state(current_view="templates")
        with patch.object(TUIKeyHandler, "_refresh_templates") as mock_ref:
            TUIKeyHandler(s).handle_key("r")
            mock_ref.assert_called_once()

    def test_quantization_view_d_detects(self):
        s = _state(current_view="quantization")
        with patch.object(TUIKeyHandler, "_detect_quantization") as mock_det:
            TUIKeyHandler(s).handle_key("d")
            mock_det.assert_called_once()

    def test_models_view_d_discovers(self):
        s = _state(current_view="models")
        with patch.object(TUIKeyHandler, "_models_discover") as mock_disc:
            TUIKeyHandler(s).handle_key("d")
            mock_disc.assert_called_once()

    def test_filestore_view_b_gets_binaries(self):
        s = _state(current_view="filestore")
        with patch.object(TUIKeyHandler, "_filestore_binaries") as mock_bin:
            TUIKeyHandler(s).handle_key("b")
            mock_bin.assert_called_once()

    def test_filestore_view_B_bootstraps(self):
        s = _state(current_view="filestore")
        with patch.object(TUIKeyHandler, "_filestore_bootstrap") as mock_boot:
            TUIKeyHandler(s).handle_key("B")
            mock_boot.assert_called_once()

    def test_health_view_r_refreshes(self):
        s = _state(current_view="health")
        with patch.object(TUIKeyHandler, "_health_refresh") as mock_ref:
            TUIKeyHandler(s).handle_key("r")
            mock_ref.assert_called_once()

    def test_selftest_view_r_runs(self):
        s = _state(current_view="selftest")
        with patch.object(TUIKeyHandler, "_selftest_run") as mock_run:
            TUIKeyHandler(s).handle_key("r")
            mock_run.assert_called_once()

    def test_loglevel_view_c_cycles(self):
        s = _state(current_view="log-level")
        with patch.object(TUIKeyHandler, "_loglevel_cycle") as mock_cyc:
            TUIKeyHandler(s).handle_key("c")
            mock_cyc.assert_called_once()

    def test_discovered_view_r_refreshes(self):
        s = _state(current_view="discovered")
        with patch.object(TUIKeyHandler, "_discovered_refresh") as mock_ref:
            TUIKeyHandler(s).handle_key("r")
            mock_ref.assert_called_once()

    def test_code_view_s_activates_search(self):
        s = _state(current_view="code")
        TUIKeyHandler(s).handle_key("s")
        assert s["input_mode"] == "code_search"

    def test_code_view_g_activates_graph(self):
        s = _state(current_view="code")
        TUIKeyHandler(s).handle_key("g")
        assert s["input_mode"] == "code_graph"

    def test_V_toggles_verbose(self):
        s = _state(current_view="main")
        TUIKeyHandler(s).handle_key("V")
        assert s["verbose_logging"] is True

    def test_R_reloads_daemon(self):
        s = _state(current_view="main")
        with patch.object(TUIKeyHandler, "_reload_daemon") as mock_rel:
            TUIKeyHandler(s).handle_key("R")
            mock_rel.assert_called_once()

    def test_space_activates_main_menu_item(self):
        s = _state(current_view="main", input_mode=None)
        with patch.object(TUIKeyHandler, "_activate_main_menu_item") as mock_act:
            TUIKeyHandler(s).handle_key(" ")
            mock_act.assert_called_once()

    def test_space_in_view_activates_selected(self):
        s = _state(current_view="projects", input_mode=None)
        with patch.object(TUIKeyHandler, "_activate_selected") as mock_act:
            TUIKeyHandler(s).handle_key(" ")
            mock_act.assert_called_once()

    def test_enter_in_view_activates_selected(self):
        s = _state(current_view="projects", input_mode=None)
        with patch.object(TUIKeyHandler, "_activate_selected") as mock_act:
            TUIKeyHandler(s).handle_key("\r")
            mock_act.assert_called_once()


# ── cycle_dispatch_mode ────────────────────────────────────────────────────


class TestCycleDispatchMode:
    def test_cycles_through_modes(self):
        s = _state(dispatch_mode="active")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"dispatch_mode": "passive_external"}
        with patch("httpx.put", return_value=mock_resp):
            TUIKeyHandler(s)._cycle_dispatch_mode()
        assert s["dispatch_mode"] == "passive_external"

    def test_wraps_at_end_of_list(self):
        s = _state(dispatch_mode="worktree_monitor")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"dispatch_mode": "active"}
        with patch("httpx.put", return_value=mock_resp):
            TUIKeyHandler(s)._cycle_dispatch_mode()
        assert s["dispatch_mode"] == "active"

    def test_unknown_mode_defaults_to_first(self):
        s = _state(dispatch_mode="unknown_mode")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"dispatch_mode": "active"}
        with patch("httpx.put", return_value=mock_resp):
            TUIKeyHandler(s)._cycle_dispatch_mode()
        assert s["dispatch_mode"] == "active"

    def test_non_200_preserves_mode(self):
        s = _state(dispatch_mode="active")
        with patch("httpx.put", return_value=MagicMock(status_code=500)):
            TUIKeyHandler(s)._cycle_dispatch_mode()
        assert s["dispatch_mode"] == "active"
        assert "failed" in s["status_msg"]

    def test_exception_preserves_mode(self):
        s = _state(dispatch_mode="active")
        with patch("httpx.put", side_effect=Exception("bad")):
            TUIKeyHandler(s)._cycle_dispatch_mode()
        assert s["dispatch_mode"] == "active"
        assert "error" in s["status_msg"].lower() or "bad" in s["status_msg"]
