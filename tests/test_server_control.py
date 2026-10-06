from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading_analysis.server_control import BRANCH, ControlError, ServerController, safe_text


@pytest.fixture
def control(tmp_path):
    return ServerController(root=tmp_path, port=8766)


def running(control, **extra):
    return {"state": "running", "url": control.url, "health": {
        "service": "trading-analysis", "project_root": str(control.root), "port": control.port,
        "pid": 3456, "executable": "C:/different/alias/python.exe", **extra}}


def test_health_accepts_python_alias_and_virtual_environment_child(control):
    with patch.object(control, "_health", return_value=running(control)["health"]):
        assert control.status()["state"] == "running"


def test_other_project_on_same_port_is_not_ours(control):
    health = running(control, project_root=str(control.root / "another-copy"))["health"]
    with patch.object(control, "_health", return_value=health), patch.object(control, "_kill") as kill:
        assert control.status()["state"] == "occupied"
        with pytest.raises(ControlError, match="No process was stopped"):
            control.stop()
        kill.assert_not_called()


def test_duplicate_start_is_idempotent(control):
    with patch.object(control, "status", return_value=running(control)), patch("subprocess.Popen") as spawn:
        assert control.start()["state"] == "running"
        spawn.assert_not_called()


def test_occupied_port_does_not_start_or_stop_any_process(control):
    with patch.object(control, "status", return_value={"state": "occupied", "message": "busy"}), patch("subprocess.Popen") as spawn:
        with pytest.raises(ControlError, match="busy"):
            control.start()
        spawn.assert_not_called()


def test_start_readiness_uses_launch_identity_not_parent_pid(control):
    stopped = {"state": "stopped"}
    health = running(control, launch_id="known-launch")
    process = MagicMock(pid=1234)
    with patch.object(control, "status", side_effect=[stopped, health]), \
         patch("trading_analysis.server_control.uuid.uuid4", return_value=MagicMock(hex="known-launch")), \
         patch("subprocess.Popen", return_value=process) as spawn:
        assert control.start()["health"]["pid"] == 3456
        assert (control.logs / "web_8766.pid").read_text() == "3456"
        assert spawn.call_args.kwargs["env"]["TRADING_SERVER_LAUNCH_ID"] == "known-launch"
        assert spawn.call_args.kwargs["cwd"] == control.root


def test_timeout_cleans_only_process_tree_started_by_this_action(control):
    process = MagicMock(pid=1234)
    process.poll.return_value = None
    with patch.object(control, "status", return_value={"state": "stopped"}), \
         patch("subprocess.Popen", return_value=process), patch.object(control, "_kill") as kill:
        with pytest.raises(ControlError, match="did not become ready"):
            control.start(timeout=0)
        kill.assert_called_once_with(1234, tree=True)
        process.wait.assert_called_once()


def test_stale_pid_file_does_not_authorize_stopping_unknown_process(control):
    control.logs.mkdir()
    (control.logs / "web_8766.pid").write_text("7890")
    with patch.object(control, "status", return_value=running(control)), \
         patch.object(control, "_listener_pids", return_value={7890}), patch.object(control, "_kill") as kill:
        with pytest.raises(ControlError, match="identity changed"):
            control.stop()
        kill.assert_not_called()


def test_stop_targets_verified_listener_and_preserves_history(control):
    history = control.root / "trades.db"
    history.write_bytes(b"history")
    with patch.object(control, "status", return_value=running(control)), \
         patch.object(control, "_listener_pids", return_value={3456}), \
         patch.object(control, "_port_open", return_value=False), patch.object(control, "_kill") as kill:
        assert control.stop()["state"] == "stopped"
        kill.assert_called_once_with(3456)
    assert history.read_bytes() == b"history"


def test_second_launcher_cannot_run_overlapping_operation(control):
    second = ServerController(root=control.root)
    with control.operation():
        with pytest.raises(ControlError, match="in progress"):
            with second.operation():
                pytest.fail("Concurrent operation was allowed")
    with second.operation():
        pass


@pytest.mark.parametrize("branch,dirty", [("main", ""), (BRANCH, " M web/app.js")])
def test_update_preflight_preserves_wrong_branch_or_dirty_folder(control, branch, dirty):
    replies = {("git", "rev-parse", "--show-toplevel"): str(control.root),
               ("git", "branch", "--show-current"): branch,
               ("git", "status", "--porcelain", "--untracked-files=normal"): dirty}
    with patch.object(control, "_run", side_effect=lambda args, **kw: replies[tuple(args)]), \
         patch.object(control, "_stop") as stop:
        with pytest.raises(ControlError):
            control.update()
        stop.assert_not_called()


def test_update_stops_pulls_installs_and_restarts_in_order(control):
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        return "updated"
    with patch.object(control, "_check_update"), patch.object(control, "status", return_value=running(control)), \
         patch.object(control, "_stop", side_effect=lambda: calls.append("stop")), \
         patch.object(control, "_start", side_effect=lambda: calls.append("start")), patch.object(control, "_run", side_effect=command):
        control.update()
    assert calls[0] == "stop"
    assert calls[1] == ["git", "pull", "--ff-only", "origin", BRANCH]
    assert calls[2][:4] == [control.python, "-m", "pip", "install"]
    assert calls[-1] == "start"


@pytest.mark.parametrize("failure", ["pull", "pip"])
def test_update_failure_keeps_server_stopped_without_reset(control, failure):
    commands = []
    def command(args, **kwargs):
        commands.append(args)
        if failure in args:
            raise ControlError("Network unavailable")
        return "ok"
    with patch.object(control, "_check_update"), patch.object(control, "status", return_value=running(control)), \
         patch.object(control, "_stop"), patch.object(control, "_start") as start, patch.object(control, "_run", side_effect=command):
        with pytest.raises(ControlError, match="server remains stopped"):
            control.update()
        start.assert_not_called()
    assert not any("reset" in command or "stash" in command for command in commands)


def test_successful_update_does_not_start_previously_stopped_server(control):
    with patch.object(control, "_check_update"), patch.object(control, "status", return_value={"state": "stopped"}), \
         patch.object(control, "_stop") as stop, patch.object(control, "_start") as start, patch.object(control, "_run", return_value="ok"):
        control.update()
        stop.assert_not_called()
        start.assert_not_called()


def test_listener_parser_matches_exact_port(control):
    output = "TCP 127.0.0.1:8766 0.0.0.0:0 LISTENING 123\nTCP 0.0.0.0:18766 0.0.0.0:0 LISTENING 456"
    with patch.object(control, "_run", return_value=output):
        assert control._listener_pids() == {123}


def test_support_log_uses_current_port_and_redacts_credentials(control):
    from trading_analysis.desktop_launcher import support_log
    control.logs.mkdir()
    (control.logs / "web_8766.err.log").write_text("HTTPS https://user:secret@private.test/api failed")
    text = support_log(control)
    assert "Port: 8766" in text
    assert "user:secret" not in text
    assert "[redacted]" in text


def test_launcher_window_actions_and_status_rendering(control):
    tk = pytest.importorskip("tkinter")
    from trading_analysis.desktop_launcher import Launcher
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("Desktop session unavailable")
    window.withdraw()
    try:
        ui = Launcher(window, root=control.root)
        window.update_idletasks()
        ui.show_status(running(control))
        assert str(ui.buttons["start"]["state"]) == "disabled"
        assert str(ui.buttons["stop"]["state"]) == "normal"
        with patch.object(ui, "controller", return_value=control), patch("webbrowser.open") as opened:
            ui.buttons["open"].invoke()
            opened.assert_called_once_with(control.url)
        with patch.object(ui, "run") as action, patch("tkinter.messagebox.askyesno", return_value=True):
            ui.buttons["update"].invoke()
            action.assert_called_once_with("update")
        ui.show_status({"state": "stopped", "url": control.url})
        assert str(ui.buttons["stop"]["state"]) == "disabled"
        assert str(ui.buttons["start"]["state"]) == "normal"
    finally:
        window.destroy()
