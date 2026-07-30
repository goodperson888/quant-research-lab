from __future__ import annotations

import importlib.util
from pathlib import Path
import signal
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dev_manager", ROOT / "scripts" / "dev_manager.py"
)
assert SPEC and SPEC.loader
dev_manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev_manager)


def make_manager(tmp_path: Path) -> dev_manager.DevManager:
    return dev_manager.DevManager(
        api_port=18000,
        web_port=18001,
        state_dir=tmp_path / "runtime" / "dev",
        start_timeout=0.1,
        lock_timeout=0.1,
    )


def test_free_port_is_available() -> None:
    with patch.object(dev_manager, "listener_pids", return_value=[]):
        assert dev_manager.classify_port("api", 18000) == ("free", [])


def test_process_exists_treats_permission_denied_as_existing() -> None:
    with patch.object(
        dev_manager.os,
        "kill",
        side_effect=PermissionError("sandbox denied process signal check"),
    ):
        assert dev_manager.process_exists(99)


def test_restore_file_snapshot_preserves_tracked_file(tmp_path: Path) -> None:
    path = tmp_path / "next-env.d.ts"
    path.write_bytes(b"production-types")
    snapshot = dev_manager.file_snapshot(path)
    path.write_bytes(b"development-types")

    dev_manager.restore_file_snapshot(path, snapshot)

    assert path.read_bytes() == b"production-types"


def test_process_details_tolerate_sandbox_permission_errors() -> None:
    with patch.object(
        dev_manager.subprocess,
        "run",
        side_effect=PermissionError("sandbox denied process inspection"),
    ):
        assert dev_manager.command_for_pid(99) == ""
        assert dev_manager.cwd_for_pid(99) == ""
        assert "无法读取进程命令" in dev_manager.describe_process(99)


def test_managed_process_falls_back_to_project_group_listener() -> None:
    with (
        patch.object(dev_manager, "process_matches_role", return_value=True),
        patch.object(dev_manager, "command_for_pid", return_value=""),
        patch.object(dev_manager, "process_group_listens", return_value=True),
    ):
        assert dev_manager.process_is_managed(101, "api", "run-id", 18000)


def test_managed_process_rejects_missing_run_id_when_command_is_visible() -> None:
    with (
        patch.object(dev_manager, "process_matches_role", return_value=True),
        patch.object(
            dev_manager,
            "command_for_pid",
            return_value="uvicorn quant_lab.interfaces.api.app:app",
        ),
    ):
        assert not dev_manager.process_is_managed(101, "api", "run-id", 18000)


def test_existing_project_service_is_reused(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)

    def listeners(port: int) -> list[int]:
        return [101] if port == manager.api_port else [202]

    with (
        patch.object(dev_manager, "listener_pids", side_effect=listeners),
        patch.object(dev_manager, "process_matches_role", return_value=True),
        patch.object(manager, "print_running") as print_running,
    ):
        assert manager.preflight() == "reuse"
        print_running.assert_called_once_with(manager.api_port, manager.web_port)


def test_unknown_port_occupant_is_reported_without_kill(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)

    def listeners(port: int) -> list[int]:
        return [303] if port == manager.api_port else []

    with (
        patch.object(dev_manager, "listener_pids", side_effect=listeners),
        patch.object(dev_manager, "process_matches_role", return_value=False),
        patch.object(dev_manager, "report_unknown_port") as report,
        patch.object(dev_manager.os, "killpg") as killpg,
    ):
        try:
            manager.preflight()
        except dev_manager.DevManagerError:
            pass
        else:
            raise AssertionError("unknown occupant should block startup")
        report.assert_called_once_with("api", manager.api_port, [303])
        killpg.assert_not_called()


def test_stale_pid_state_is_not_running(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    state = {
        "run_id": "stale-run",
        "phase": "running",
        "api_pid": 404,
        "web_pid": 505,
        "api_port": manager.api_port,
        "web_port": manager.web_port,
    }
    manager.save_state(state)

    with patch.object(dev_manager, "process_is_managed", return_value=False):
        assert manager.managed_state_is_running(manager.load_state()) is False

    manager.clear_state("stale-run")
    assert manager.load_state() is None


def test_safe_stop_rejects_unconfirmed_process(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    state = {"run_id": "expected-run", "api_pid": 606}

    with (
        patch.object(dev_manager, "process_is_managed", return_value=False),
        patch.object(dev_manager.os, "killpg") as killpg,
    ):
        assert manager.terminate_managed_group(state, "api") is False
        killpg.assert_not_called()


def test_safe_stop_targets_confirmed_process_group(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    state = {"run_id": "expected-run", "api_pid": 606}

    with (
        patch.object(dev_manager, "process_is_managed", return_value=True),
        patch.object(dev_manager.os, "getpgid", return_value=606),
        patch.object(dev_manager.os, "killpg") as killpg,
    ):
        assert manager.terminate_managed_group(state, "api") is True
        killpg.assert_called_once_with(606, signal.SIGTERM)


def test_stop_refuses_unmanaged_project_service(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)

    with (
        patch.object(
            dev_manager,
            "classify_port",
            side_effect=[("project", [707]), ("free", [])],
        ),
        patch.object(dev_manager.os, "killpg") as killpg,
    ):
        try:
            manager.stop()
        except dev_manager.DevManagerError:
            pass
        else:
            raise AssertionError("unmanaged project service should not be stopped")
        killpg.assert_not_called()
