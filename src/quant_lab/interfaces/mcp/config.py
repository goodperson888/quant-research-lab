from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


MCP_SERVER_NAME = "quant-research-lab"


def _development_launch(root: Path) -> tuple[str, list[str]]:
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        python = Path(sys.executable).resolve()
    return str(python), ["-m", "quant_lab.interfaces.mcp.server"]


def mcp_launch_spec(root: Path) -> tuple[str, list[str]]:
    configured_command = os.environ.get("QUANT_LAB_MCP_COMMAND")
    configured_args = os.environ.get("QUANT_LAB_MCP_ARGS_JSON")
    if configured_command:
        try:
            parsed = json.loads(configured_args or "[]")
        except json.JSONDecodeError:
            parsed = []
        if not isinstance(parsed, list) or not all(
            isinstance(item, str) for item in parsed
        ):
            parsed = []
        return configured_command, list(parsed)
    return _development_launch(root)


def _shell_join(command: str, args: list[str]) -> str:
    values = [command, *args]
    if os.name == "nt":
        return subprocess.list2cmdline(values)
    return shlex.join(values)


def mcp_connection_info(root: Path) -> dict[str, Any]:
    command, args = mcp_launch_spec(root.resolve())
    launch = _shell_join(command, args)
    codex_add = _shell_join(
        "codex",
        ["mcp", "add", MCP_SERVER_NAME, "--", command, *args],
    )
    return {
        "available": Path(command).is_file(),
        "server_name": MCP_SERVER_NAME,
        "transport": "stdio",
        "command": command,
        "args": args,
        "launch_preview": launch,
        "codex": {
            "install_command": codex_add,
            "verify_command": f"codex mcp get {MCP_SERVER_NAME}",
            "remove_command": f"codex mcp remove {MCP_SERVER_NAME}",
        },
        "claude": {
            "config": {
                "mcpServers": {
                    MCP_SERVER_NAME: {
                        "command": command,
                        "args": args,
                    }
                }
            }
        },
        "requirements": [
            "先安装并启动 Quant Research Lab",
            "客户的本地 AI 客户端必须支持 MCP stdio",
            "商业许可证需要包含 local_ai 功能",
            "首次添加配置后通常需要重启 AI 客户端",
        ],
        "privacy_note": (
            "MCP 只启动本机进程并访问本机研究数据；不开放公网端口，"
            "也不向 AI 暴露任意 Shell、密钥或实盘工具。"
        ),
    }
