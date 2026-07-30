#!/usr/bin/env python3
"""Single-instance manager for the local API and Web development servers."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Iterable
import uuid


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = ROOT / "runtime" / "dev"
ROLE_LABELS = {"api": "API", "web": "Web"}


class DevManagerError(RuntimeError):
    pass


class Interrupted(RuntimeError):
    pass


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError):
        return False
    except OSError as error:
        return error.errno == 1
    return True


def command_for_pid(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "eww", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def cwd_for_pid(pid: int) -> str:
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return next(
        (line[1:] for line in result.stdout.splitlines() if line.startswith("n")),
        "",
    )


def is_within(path: str, parent: Path) -> bool:
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def process_matches_role(pid: int, role: str) -> bool:
    if not process_exists(pid):
        return False
    command = command_for_pid(pid)
    cwd = cwd_for_pid(pid)
    root_text = str(ROOT)

    if role == "api":
        command_matches = (
            "uvicorn" in command
            and "quant_lab.interfaces.api.app:app" in command
        )
        location_matches = is_within(cwd, ROOT) or root_text in command
    elif role == "web":
        command_matches = "next" in command or (
            "npm" in command and "run dev" in command
        )
        location_matches = (
            is_within(cwd, ROOT / "apps" / "web")
            or str(ROOT / "apps" / "web") in command
        )
    else:
        return False
    if command:
        return command_matches and location_matches
    return location_matches


def process_has_run_id(pid: int, run_id: str) -> bool:
    return bool(run_id) and f"QUANT_LAB_DEV_INSTANCE_ID={run_id}" in command_for_pid(pid)


def process_group_listens(pid: int, port: int) -> bool:
    try:
        target_pgid = os.getpgid(pid)
    except OSError:
        return False
    for listener_pid in listener_pids(port):
        try:
            if os.getpgid(listener_pid) == target_pgid:
                return True
        except OSError:
            continue
    return False


def process_is_managed(
    pid: int,
    role: str,
    run_id: str,
    port: int | None = None,
) -> bool:
    if not process_matches_role(pid, role):
        return False
    command = command_for_pid(pid)
    if command:
        return bool(run_id) and f"QUANT_LAB_DEV_INSTANCE_ID={run_id}" in command
    return port is not None and process_group_listens(pid, port)


def listener_pids(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        check=False,
        capture_output=True,
        text=True,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        with contextlib.suppress(ValueError):
            pids.append(int(line.strip()))
    return sorted(set(pids))


def classify_port(role: str, port: int) -> tuple[str, list[int]]:
    pids = listener_pids(port)
    if not pids:
        return "free", []
    if all(process_matches_role(pid, role) for pid in pids):
        return "project", pids
    return "unknown", pids


def managed_listener_exists(role: str, port: int, run_id: str) -> bool:
    return any(
        process_is_managed(pid, role, run_id, port)
        for pid in listener_pids(port)
    )


def describe_process(pid: int) -> str:
    command = command_for_pid(pid) or "无法读取进程命令"
    cwd = cwd_for_pid(pid)
    detail = f"  PID {pid}: {command}"
    if cwd:
        detail += f"\n    工作目录: {cwd}"
    return detail


def report_unknown_port(role: str, port: int, pids: Iterable[int]) -> None:
    label = ROLE_LABELS[role]
    print(
        f"{label} 端口 {port} 已被其他程序占用，本脚本没有终止任何进程。",
        file=sys.stderr,
    )
    for pid in pids:
        print(describe_process(pid), file=sys.stderr)
    env_name = "QUANT_LAB_API_PORT" if role == "api" else "QUANT_LAB_WEB_PORT"
    print(
        "处理建议：先确认以上进程用途；若是手动启动的旧服务，请回到原终端按 Ctrl+C。",
        file=sys.stderr,
    )
    print(
        f"也可以改用空闲端口，例如 {env_name}=<端口> ./scripts/dev.sh。",
        file=sys.stderr,
    )


def file_snapshot(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def restore_file_snapshot(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    try:
        if path.read_bytes() == snapshot:
            return
    except FileNotFoundError:
        pass
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(snapshot)
    os.replace(temporary, path)


class StateLock:
    def __init__(self, state_dir: Path, timeout: float) -> None:
        self.state_dir = state_dir
        self.timeout = timeout
        self.handle: Any = None

    def __enter__(self) -> "StateLock":
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.handle = (self.state_dir / "lock").open("a+")
        deadline = time.monotonic() + self.timeout
        announced = False
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if not announced:
                    print("另一个窗口正在执行启动/停止操作，正在等待...")
                    announced = True
                if time.monotonic() >= deadline:
                    self.handle.close()
                    raise DevManagerError("等待本地开发服务锁超时，请稍后重试。")
                time.sleep(0.1)

    def __exit__(self, *args: object) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


class DevManager:
    def __init__(
        self,
        *,
        api_port: int,
        web_port: int,
        state_dir: Path,
        start_timeout: float,
        lock_timeout: float,
    ) -> None:
        self.api_port = api_port
        self.web_port = web_port
        self.state_dir = state_dir
        self.state_file = state_dir / "state.json"
        self.start_timeout = start_timeout
        self.lock_timeout = lock_timeout

    def lock(self) -> StateLock:
        return StateLock(self.state_dir, self.lock_timeout)

    def load_state(self) -> dict[str, Any] | None:
        try:
            state = json.loads(self.state_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return state if isinstance(state, dict) else None

    def save_state(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.state_dir / f".state-{os.getpid()}.tmp"
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        )
        os.replace(temporary, self.state_file)

    def clear_state(self, run_id: str | None = None) -> None:
        state = self.load_state()
        if run_id and state and state.get("run_id") != run_id:
            return
        with contextlib.suppress(FileNotFoundError):
            self.state_file.unlink()

    @staticmethod
    def state_pid(state: dict[str, Any], role: str) -> int:
        value = state.get(f"{role}_pid")
        return value if isinstance(value, int) and value > 0 else 0

    @staticmethod
    def state_port(state: dict[str, Any], role: str) -> int:
        value = state.get(f"{role}_port")
        return value if isinstance(value, int) and value > 0 else 0

    def managed_state_is_running(self, state: dict[str, Any] | None) -> bool:
        if not state or state.get("phase") != "running":
            return False
        run_id = state.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return False
        for role in ("api", "web"):
            pid = self.state_pid(state, role)
            port = self.state_port(state, role)
            if (
                not process_is_managed(pid, role, run_id, port)
                or not managed_listener_exists(role, port, run_id)
            ):
                return False
        return True

    def print_running(self, api_port: int, web_port: int) -> None:
        print("Quant Research Lab 已经运行，可直接访问：")
        print(f"Web Studio: http://127.0.0.1:{web_port}/studio")
        print(f"API health: http://127.0.0.1:{api_port}/health")
        print("停止：./scripts/dev.sh stop    重启：./scripts/dev.sh restart")

    def preflight(self) -> str:
        api_class, api_pids = classify_port("api", self.api_port)
        web_class, web_pids = classify_port("web", self.web_port)

        if api_class == "unknown":
            report_unknown_port("api", self.api_port, api_pids)
        if web_class == "unknown":
            report_unknown_port("web", self.web_port, web_pids)
        if "unknown" in (api_class, web_class):
            raise DevManagerError("端口预检失败，未启动或终止任何进程。")
        if "project" in (api_class, web_class):
            print(
                "检测到本项目手动启动或失去状态文件的旧服务；"
                "正在安全停止并重新纳入 dev.sh 管理。"
            )
            self.stop_unmanaged_project_services(
                classified={
                    "api": (api_class, api_pids),
                    "web": (web_class, web_pids),
                },
                announce=False,
            )
        return "start"

    def terminate_verified_listeners(
        self,
        *,
        role: str,
        port: int,
        pids: Iterable[int],
    ) -> bool:
        verified = sorted(set(pids))
        if not verified:
            return False
        current_listeners = set(listener_pids(port))
        for pid in verified:
            if pid not in current_listeners:
                continue
            if not process_matches_role(pid, role):
                raise DevManagerError(
                    f"{ROLE_LABELS[role]} 端口 {port} 的进程身份在停止前发生变化；"
                    "本次没有继续终止。"
                )
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except PermissionError as exc:
                raise DevManagerError(
                    f"没有权限停止已确认的本项目 {ROLE_LABELS[role]} 进程 PID {pid}。"
                ) from exc

        deadline = time.monotonic() + 5.0
        while listener_pids(port) and time.monotonic() < deadline:
            time.sleep(0.1)
        remaining = listener_pids(port)
        if remaining:
            raise DevManagerError(
                f"已向本项目 {ROLE_LABELS[role]} 发送停止信号，但端口 {port} "
                f"仍被 PID {', '.join(str(pid) for pid in remaining)} 占用；"
                "未使用强制终止。"
            )
        return True

    def stop_unmanaged_project_services(
        self,
        *,
        classified: dict[str, tuple[str, list[int]]] | None = None,
        announce: bool,
    ) -> bool:
        results = classified or {
            "api": classify_port("api", self.api_port),
            "web": classify_port("web", self.web_port),
        }
        for role, (classification, pids) in results.items():
            if classification == "unknown":
                port = self.api_port if role == "api" else self.web_port
                report_unknown_port(role, port, pids)
                raise DevManagerError(
                    "端口上存在无法确认身份的进程，本次没有终止任何未知软件。"
                )

        stopped = False
        for role in ("web", "api"):
            classification, pids = results[role]
            if classification != "project":
                continue
            port = self.api_port if role == "api" else self.web_port
            stopped = (
                self.terminate_verified_listeners(
                    role=role,
                    port=port,
                    pids=pids,
                )
                or stopped
            )
        if stopped and announce:
            print("已停止可确认属于本项目的 API/Web 服务。")
        return stopped

    def terminate_managed_group(
        self, state: dict[str, Any], role: str
    ) -> bool:
        run_id = state.get("run_id")
        pid = self.state_pid(state, role)
        port = self.state_port(state, role)
        if (
            not isinstance(run_id, str)
            or not process_is_managed(pid, role, run_id, port)
        ):
            return False
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return False
        if pgid != pid:
            return False
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        except PermissionError as exc:
            raise DevManagerError(
                f"当前窗口没有权限停止已确认的本项目 {ROLE_LABELS[role]} "
                f"进程组 PID {pid}。请在你自己的终端运行同一条命令。"
            ) from exc
        return True

    def wait_for_pid_exit(self, pid: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while process_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        return not process_exists(pid)

    def stop_state(self, state: dict[str, Any], *, announce: bool) -> bool:
        stopped = False
        tracked: list[int] = []
        for role in ("web", "api"):
            pid = self.state_pid(state, role)
            if self.terminate_managed_group(state, role):
                stopped = True
                tracked.append(pid)

        if any(not self.wait_for_pid_exit(pid) for pid in tracked):
            raise DevManagerError(
                "已发送停止信号，但仍有本项目进程未退出；未使用强制终止，请查看原启动终端输出。"
            )
        self.clear_state(
            state.get("run_id") if isinstance(state.get("run_id"), str) else None
        )
        if stopped and announce:
            print("Quant Research Lab API 和 Web 已停止。")
        return stopped

    def stop(self) -> int:
        with self.lock():
            state = self.load_state()
            if state and self.stop_state(state, announce=True):
                return 0

            if self.stop_unmanaged_project_services(announce=True):
                self.clear_state()
                return 0
            print("没有发现 Quant Research Lab 本地服务。")
        return 0

    def wait_until_ready(
        self,
        process: subprocess.Popen[Any],
        role: str,
        port: int,
        run_id: str,
    ) -> None:
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise DevManagerError(f"{ROLE_LABELS[role]} 启动进程已提前退出。")
            if process_group_listens(process.pid, port) or managed_listener_exists(
                role, port, run_id
            ):
                return
            pids = listener_pids(port)
            if pids:
                report_unknown_port(role, port, pids)
                raise DevManagerError(
                    f"{ROLE_LABELS[role]} 启动期间端口被其他进程占用。"
                )
            time.sleep(0.2)
        raise DevManagerError(
            f"{ROLE_LABELS[role]} 在 {self.start_timeout:g} 秒内未监听 "
            f"127.0.0.1:{port}，启动失败。"
        )

    def launch(self, command: list[str], env: dict[str, str]) -> subprocess.Popen[Any]:
        return subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            start_new_session=True,
        )

    def cleanup_started(
        self,
        state: dict[str, Any],
        processes: Iterable[subprocess.Popen[Any]],
    ) -> None:
        for role in ("web", "api"):
            self.terminate_managed_group(state, role)
        remaining = False
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                remaining = True
        if remaining:
            print(
                "本次启动的服务收到停止信号后仍未完全退出；"
                "保留 runtime/dev/state.json 供安全重试 stop。",
                file=sys.stderr,
            )
        else:
            self.clear_state(state.get("run_id"))

    def start(self) -> int:
        processes: list[subprocess.Popen[Any]] = []
        state: dict[str, Any] = {}
        next_env_path = ROOT / "apps" / "web" / "next-env.d.ts"
        next_env_snapshot = file_snapshot(next_env_path)

        with self.lock():
            existing = self.load_state()
            if self.managed_state_is_running(existing):
                assert existing is not None
                self.print_running(
                    self.state_port(existing, "api"),
                    self.state_port(existing, "web"),
                )
                return 0
            if existing:
                self.stop_state(existing, announce=False)
            if self.preflight() == "reuse":
                return 0

            run_id = uuid.uuid4().hex
            base_env = os.environ.copy()
            base_env["QUANT_LAB_DEV_INSTANCE_ID"] = run_id
            state = {
                "run_id": run_id,
                "phase": "starting",
                "supervisor_pid": os.getpid(),
                "api_port": self.api_port,
                "web_port": self.web_port,
            }
            self.save_state(state)

            try:
                print("正在启动 Quant Research Lab...")
                api_env = base_env.copy()
                api_env["QUANT_LAB_API_PORT"] = str(self.api_port)
                api_env["QUANT_LAB_WEB_PORT"] = str(self.web_port)
                api = self.launch([str(ROOT / "scripts" / "dev-api.sh")], api_env)
                processes.append(api)
                state["api_pid"] = api.pid
                self.save_state(state)
                self.wait_until_ready(api, "api", self.api_port, run_id)

                web_env = base_env.copy()
                web_env["NEXT_PUBLIC_QUANT_LAB_API_URL"] = (
                    f"http://127.0.0.1:{self.api_port}"
                )
                web = self.launch(
                    [
                        str(ROOT / "scripts" / "dev-web.sh"),
                        "--port",
                        str(self.web_port),
                    ],
                    web_env,
                )
                processes.append(web)
                state["web_pid"] = web.pid
                self.save_state(state)
                self.wait_until_ready(web, "web", self.web_port, run_id)
                time.sleep(0.5)
                restore_file_snapshot(next_env_path, next_env_snapshot)
                state["phase"] = "running"
                self.save_state(state)
            except BaseException:
                restore_file_snapshot(next_env_path, next_env_snapshot)
                self.cleanup_started(state, processes)
                raise

        print("启动完成：")
        print(f"Web Studio: http://127.0.0.1:{self.web_port}/studio")
        print(f"API health: http://127.0.0.1:{self.api_port}/health")
        print("按 Ctrl+C 可同时停止 API 和 Web。")
        print("其他终端可运行：./scripts/dev.sh stop 或 ./scripts/dev.sh restart")

        try:
            while all(process.poll() is None for process in processes):
                time.sleep(0.5)
            failed = next(
                (process for process in processes if process.poll() is not None),
                processes[0],
            )
            role = "API" if failed is processes[0] else "Web"
            print(f"{role} 已停止，正在关闭另一服务。", file=sys.stderr)
            return failed.returncode or 0
        except (KeyboardInterrupt, Interrupted):
            return 130
        finally:
            restore_file_snapshot(next_env_path, next_env_snapshot)
            self.cleanup_started(state, processes)

    def status(self) -> int:
        with self.lock():
            state = self.load_state()
            if self.managed_state_is_running(state):
                assert state is not None
                self.print_running(
                    self.state_port(state, "api"),
                    self.state_port(state, "web"),
                )
                return 0

            api_class, api_pids = classify_port("api", self.api_port)
            web_class, web_pids = classify_port("web", self.web_port)
            if "unknown" in (api_class, web_class):
                if api_class == "unknown":
                    report_unknown_port("api", self.api_port, api_pids)
                if web_class == "unknown":
                    report_unknown_port("web", self.web_port, web_pids)
                return 1
            if "project" in (api_class, web_class):
                print(
                    "发现本项目手动启动或失去状态文件的服务。"
                    "可直接运行 ./scripts/dev.sh start 重新纳管，"
                    "或运行 ./scripts/dev.sh stop 安全停止。"
                )
                return 0
            print("没有发现 Quant Research Lab 本地服务。")
        return 0


def positive_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1 到 65535 之间")
    return port


def positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("超时必须大于 0")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="管理 Quant Research Lab 本地单实例 API/Web 服务"
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("start", "stop", "restart", "status"),
        default="start",
    )
    parser.add_argument(
        "--api-port",
        type=positive_port,
        default=os.environ.get("QUANT_LAB_API_PORT", "8000"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--web-port",
        type=positive_port,
        default=os.environ.get("QUANT_LAB_WEB_PORT", "3000"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(
            os.environ.get("QUANT_LAB_DEV_STATE_DIR", str(DEFAULT_STATE_DIR))
        ),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--start-timeout",
        type=positive_float,
        default=os.environ.get("QUANT_LAB_DEV_START_TIMEOUT", "30"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--lock-timeout",
        type=positive_float,
        default=os.environ.get("QUANT_LAB_DEV_LOCK_TIMEOUT", "70"),
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def handle_signal(_signum: int, _frame: object) -> None:
    raise Interrupted


def main() -> int:
    args = parse_args()
    manager = DevManager(
        api_port=args.api_port,
        web_port=args.web_port,
        state_dir=args.state_dir,
        start_timeout=args.start_timeout,
        lock_timeout=args.lock_timeout,
    )
    try:
        if args.action == "stop":
            return manager.stop()
        if args.action == "restart":
            manager.stop()
        if args.action == "status":
            return manager.status()
        return manager.start()
    except KeyboardInterrupt:
        return 130
    except Interrupted:
        return 130
    except DevManagerError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGHUP, handle_signal)
    raise SystemExit(main())
