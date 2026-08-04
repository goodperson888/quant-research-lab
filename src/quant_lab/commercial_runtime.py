from __future__ import annotations

import argparse
from contextlib import suppress
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
import uuid
import webbrowser


APP_NAME = "Quant Research Lab"
APP_SUPPORT_DIR = "QuantResearchLab"
DEFAULT_API_PORT = 8100
DEFAULT_WEB_PORT = 3100
RUNTIME_DIR = Path("runtime") / "commercial"
STATE_FILE = RUNTIME_DIR / "supervisor.json"
LOG_FILE = RUNTIME_DIR / "commercial-runtime.log"
STOP_REQUEST_FILE = RUNTIME_DIR / "stop-request.json"

PROJECT_DIRECTORIES = (
    "data/raw",
    "data/interim",
    "data/processed",
    "data/external",
    "data/manifests",
    "data/catalog",
    "experiments/runs",
    "factor_library/candidates",
    "factor_library/validated",
    "factor_library/production",
    "factor_library/degraded",
    "factor_library/retired",
    "factor_library/rejected",
    "reports/backtests",
    "reports/correctness",
    "reports/data_quality",
    "reports/daily",
    "reports/diagnostics",
    "reports/experiments",
    "reports/proposals",
    "reports/risk",
    "reports/weekly",
    "runtime/agent-connector",
    "runtime/agent-runs",
    "runtime/app",
    "runtime/commercial",
    "strategies/freqtrade",
    "strategies/inbox",
    "strategies/pine",
    "strategies/research",
)


class CommercialRuntimeError(RuntimeError):
    pass


def _compiled_runtime() -> bool:
    return "__compiled__" in globals()


def bundle_contents_dir() -> Path:
    configured = os.environ.get("QUANT_LAB_BUNDLE_CONTENTS")
    if configured:
        return Path(configured).expanduser().resolve()
    executable = Path(sys.executable).resolve()
    if executable.parent.name == "MacOS" and executable.parent.parent.name == "Contents":
        return executable.parent.parent
    return executable.parent


def bundle_resources_dir() -> Path:
    return bundle_contents_dir() / "Resources"


def default_data_home() -> Path:
    configured = os.environ.get("QUANT_LAB_DATA_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / APP_SUPPORT_DIR
        )
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / APP_SUPPORT_DIR
    return Path.home() / ".local" / "share" / APP_SUPPORT_DIR


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _copy_missing_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise CommercialRuntimeError(f"安装包缺少项目模板：{source}")
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def seed_project_home(data_home: Path, resources: Path) -> None:
    data_home.mkdir(parents=True, exist_ok=True)
    _copy_missing_tree(resources / "project-template", data_home)
    for relative in PROJECT_DIRECTORIES:
        (data_home / relative).mkdir(parents=True, exist_ok=True)

    build_manifest = _read_json(resources / "build-manifest.json")
    if build_manifest is not None:
        _atomic_json_write(
            data_home / "runtime" / "product-build.json",
            build_manifest,
        )

    from quant_lab.infrastructure.sqlite_product_repository import (
        SQLiteProductRepository,
    )
    from quant_lab.registry import initialize as initialize_factor_registry

    SQLiteProductRepository(
        data_home / "runtime" / "app" / "quant_lab.sqlite3"
    ).initialize()
    initialize_factor_registry(data_home / "factor_library" / "registry.sqlite3")


def _runtime_path_entries() -> list[str]:
    home = Path.home()
    if os.name == "nt":
        return [
            str(Path(sys.executable).resolve().parent),
            str(home / "AppData" / "Local" / "Programs"),
        ]
    return [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(home / ".local" / "bin"),
        str(home / ".npm-global" / "bin"),
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]


def configure_environment(
    *,
    data_home: Path,
    resources: Path,
    api_port: int,
    web_port: int,
) -> dict[str, str]:
    public_key = resources / "licensing" / "public-key.pem"
    if not public_key.is_file():
        raise CommercialRuntimeError("安装包缺少商业授权公钥")
    env = os.environ.copy()
    env.update(
        {
            "QUANT_LAB_HOME": str(data_home),
            "QUANT_LAB_DATA_HOME": str(data_home),
            "QUANT_LAB_LICENSE_ENFORCEMENT": "commercial_required",
            "QUANT_LAB_LICENSE_PUBLIC_KEY_PATH": str(public_key),
            "QUANT_LAB_API_PORT": str(api_port),
            "QUANT_LAB_WEB_PORT": str(web_port),
            "NEXT_PUBLIC_QUANT_LAB_API_URL": f"http://127.0.0.1:{api_port}",
            "HOSTNAME": "127.0.0.1",
            "PORT": str(web_port),
            "PYTHONUNBUFFERED": "1",
        }
    )
    existing_path = [
        value for value in env.get("PATH", "").split(os.pathsep) if value
    ]
    env["PATH"] = os.pathsep.join(
        dict.fromkeys([*_runtime_path_entries(), *existing_path])
    )
    self_command = _self_command()
    env["QUANT_LAB_MCP_COMMAND"] = self_command[0]
    env["QUANT_LAB_MCP_ARGS_JSON"] = json.dumps(
        [
            *self_command[1:],
            "--role",
            "mcp",
            "--data-home",
            str(data_home),
        ],
        ensure_ascii=False,
    )
    return env


def _self_command() -> list[str]:
    if _compiled_runtime():
        return [sys.executable]
    return [sys.executable, "-m", "quant_lab.commercial_runtime"]


def _run_api(api_port: int) -> int:
    import uvicorn

    from quant_lab.interfaces.api.app import create_app

    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=api_port,
        access_log=False,
        log_level="info",
    )
    return 0


def _run_worker() -> int:
    from quant_lab.workers.cli import build_worker, run_watch

    return run_watch(build_worker(), poll_seconds=1.0)


def _run_connector() -> int:
    from quant_lab.agents.cli import main as connector_main

    previous = sys.argv
    try:
        sys.argv = ["quant-lab-agent-connector", "--watch"]
        return connector_main()
    finally:
        sys.argv = previous


def _run_mcp() -> int:
    from quant_lab.interfaces.mcp.server import main as mcp_main

    return mcp_main([])


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            process = ctypes.windll.kernel32.OpenProcess(
                0x1000,
                False,
                pid,
            )
            if not process:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                    process, ctypes.byref(exit_code)
                ):
                    return False
                return exit_code.value == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _windows_process_image(pid: int) -> str | None:
    if os.name != "nt" or not _pid_alive(pid):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return None
        try:
            capacity = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(capacity.value)
            if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
                process,
                0,
                buffer,
                ctypes.byref(capacity),
            ):
                return None
            return buffer.value
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    except (AttributeError, OSError):
        return None


def _process_matches(pid: int, executable: str) -> bool:
    if not executable or not _pid_alive(pid):
        return False
    if os.name == "nt":
        process_image = _windows_process_image(pid)
        if process_image is None:
            return False
        return os.path.normcase(str(Path(process_image).resolve())) == os.path.normcase(
            str(Path(executable).resolve())
        )
    try:
        completed = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return executable in completed.stdout


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_http(url: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "服务尚未就绪"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.5) as response:
                if 200 <= response.status < 500:
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, URLError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise CommercialRuntimeError(f"本地服务启动超时：{url}（{last_error}）")


def _open_studio(web_port: int) -> None:
    webbrowser.open(f"http://127.0.0.1:{web_port}/studio")


def _show_macos_message(title: str, message: str) -> None:
    if sys.platform != "darwin":
        return
    script = (
        'on run argv\n'
        'display dialog (item 2 of argv) with title (item 1 of argv) '
        'buttons {"确定"} default button "确定"\n'
        'end run'
    )
    with suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["/usr/bin/osascript", "-e", script, title, message],
            check=False,
            timeout=30,
        )


def _show_windows_message(title: str, message: str) -> None:
    if os.name != "nt":
        return
    with suppress(AttributeError, OSError):
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=3)


def _stop_from_state(data_home: Path) -> int:
    state_path = data_home / STATE_FILE
    state = _read_json(state_path)
    if state is None:
        print("Quant Research Lab 当前没有运行。")
        return 0
    pid = int(state.get("supervisor_pid", 0))
    executable = str(state.get("executable", ""))
    current = str(Path(sys.executable).resolve())
    if executable != current or not _process_matches(pid, current):
        state_path.unlink(missing_ok=True)
        print("已清理失效的商业运行状态。")
        return 0
    stop_request = data_home / STOP_REQUEST_FILE
    _atomic_json_write(
        stop_request,
        {
            "schema_version": 1,
            "requested_at_epoch": time.time(),
            "requested_by_pid": os.getpid(),
        },
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    if _pid_alive(pid):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                timeout=10,
            )
        else:
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.2)
    stop_request.unlink(missing_ok=True)
    if _pid_alive(pid):
        raise CommercialRuntimeError("服务停止超时，请在系统任务管理器中退出应用")
    print("Quant Research Lab 已停止。")
    return 0


def _status(data_home: Path, web_port: int) -> int:
    state = _read_json(data_home / STATE_FILE)
    if state and _process_matches(
        int(state.get("supervisor_pid", 0)),
        str(state.get("executable", "")),
    ):
        print(
            json.dumps(
                {
                    "running": True,
                    "web_studio": f"http://127.0.0.1:{web_port}/studio",
                    "data_home": str(data_home),
                    **state,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(
        json.dumps(
            {"running": False, "data_home": str(data_home)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1


def _run_supervisor(
    *,
    data_home: Path,
    resources: Path,
    api_port: int,
    web_port: int,
    open_browser: bool,
) -> int:
    env = configure_environment(
        data_home=data_home,
        resources=resources,
        api_port=api_port,
        web_port=web_port,
    )
    seed_project_home(data_home, resources)
    state_path = data_home / STATE_FILE
    state = _read_json(state_path)
    current_executable = str(Path(sys.executable).resolve())
    if state is not None and _process_matches(
        int(state.get("supervisor_pid", 0)),
        str(state.get("executable", "")),
    ):
        if open_browser:
            _open_studio(web_port)
        return 0
    state_path.unlink(missing_ok=True)
    stop_request = data_home / STOP_REQUEST_FILE
    stop_request.unlink(missing_ok=True)

    occupied = [
        port for port in (api_port, web_port) if not _port_available(port)
    ]
    if occupied:
        raise CommercialRuntimeError(
            "本机端口被其他程序占用：" + "、".join(str(port) for port in occupied)
        )

    node = (
        resources
        / "node"
        / "bin"
        / ("node.exe" if os.name == "nt" else "node")
    )
    web_server = resources / "web" / "server.js"
    if not node.is_file() or not web_server.is_file():
        raise CommercialRuntimeError("安装包缺少网页运行时")

    log_path = data_home / LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] starting commercial runtime\n")
    log.flush()
    processes: list[tuple[str, subprocess.Popen[Any]]] = []
    command = _self_command()
    common_args = [
        "--data-home",
        str(data_home),
        "--api-port",
        str(api_port),
        "--web-port",
        str(web_port),
    ]
    stopping = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    try:
        for role in ("api", "worker", "connector"):
            process = subprocess.Popen(
                [*command, "--role", role, *common_args],
                env=env,
                cwd=data_home,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes.append((role, process))
        web = subprocess.Popen(
            [str(node), str(web_server)],
            env=env,
            cwd=resources / "web",
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(("web", web))
        _atomic_json_write(
            state_path,
            {
                "schema_version": 1,
                "phase": "starting",
                "supervisor_pid": os.getpid(),
                "executable": current_executable,
                "api_port": api_port,
                "web_port": web_port,
                "children": {
                    role: process.pid for role, process in processes
                },
                "started_at_epoch": time.time(),
            },
        )
        _wait_http(
            f"http://127.0.0.1:{api_port}/health", timeout_seconds=45
        )
        _wait_http(
            f"http://127.0.0.1:{web_port}/studio", timeout_seconds=45
        )
        ready_state = _read_json(state_path) or {}
        ready_state["phase"] = "running"
        _atomic_json_write(state_path, ready_state)
        if open_browser:
            _open_studio(web_port)

        while not stopping:
            if stop_request.is_file():
                stopping = True
                continue
            failed = next(
                (
                    (role, process.returncode)
                    for role, process in processes
                    if process.poll() is not None
                ),
                None,
            )
            if failed is not None:
                raise CommercialRuntimeError(
                    f"{failed[0]} 服务意外停止，退出码 {failed[1]}"
                )
            time.sleep(0.5)
        return 0
    finally:
        for _role, process in reversed(processes):
            _stop_process(process)
        state_path.unlink(missing_ok=True)
        stop_request.unlink(missing_ok=True)
        log.close()
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant-research-lab-commercial")
    parser.add_argument(
        "--role",
        choices=("supervisor", "api", "worker", "connector", "mcp"),
        default="supervisor",
    )
    parser.add_argument("--data-home")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = build_parser().parse_args(argv)
    data_home = (
        Path(args.data_home).expanduser().resolve()
        if args.data_home
        else default_data_home()
    )
    resources = bundle_resources_dir()
    if args.stop:
        return _stop_from_state(data_home)
    if args.status:
        return _status(data_home, args.web_port)
    configured_environment = configure_environment(
        data_home=data_home,
        resources=resources,
        api_port=args.api_port,
        web_port=args.web_port,
    )
    os.environ.update(configured_environment)
    if args.role == "api":
        return _run_api(args.api_port)
    if args.role == "worker":
        return _run_worker()
    if args.role == "connector":
        return _run_connector()
    if args.role == "mcp":
        seed_project_home(data_home, resources)
        return _run_mcp()
    return _run_supervisor(
        data_home=data_home,
        resources=resources,
        api_port=args.api_port,
        web_port=args.web_port,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CommercialRuntimeError as exc:
        print(f"{APP_NAME} 启动失败：{exc}", file=sys.stderr)
        _show_macos_message(f"{APP_NAME} 启动失败", str(exc))
        _show_windows_message(f"{APP_NAME} 启动失败", str(exc))
        raise SystemExit(2)
