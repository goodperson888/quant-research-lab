from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping

from quant_lab.infrastructure.llm import (
    formalization_prompt,
    validate_formalization_result,
)


KNOWN_CODEX_PATH = Path("/Applications/ChatGPT.app/Contents/Resources/codex")


def resolve_codex_binary() -> Path | None:
    configured = os.environ.get("QUANT_LAB_CODEX_BIN")
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() else None
    discovered = shutil.which("codex")
    if discovered:
        return Path(discovered)
    return KNOWN_CODEX_PATH if KNOWN_CODEX_PATH.is_file() else None


class LocalCodexFormalizer:
    """Run one fixed, read-only Codex formalization task."""

    def __init__(self, root: Path, *, timeout_seconds: int = 900) -> None:
        self.root = root.resolve()
        self.timeout_seconds = timeout_seconds

    def available(self) -> bool:
        return resolve_codex_binary() is not None

    def propose_formalization(
        self, *, raw_content: str, agent_run_id: str
    ) -> Mapping[str, Any]:
        codex = resolve_codex_binary()
        if codex is None:
            raise RuntimeError(
                "未找到 Codex CLI；请用 ./scripts/dev.sh 启动已安装 Codex 的本机环境"
            )
        schema_path = self.root / "configs/agents/formalization-output.schema.json"
        if not schema_path.is_file():
            raise RuntimeError("缺少固定的策略形式化输出 Schema")
        run_dir = self.root / "runtime" / "agent-runs" / agent_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        output_path = run_dir / "final.json"
        command = [
            str(codex),
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--cd",
            str(self.root),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        completed = subprocess.run(
            command,
            input=formalization_prompt(raw_content),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                f"Codex 本地助手执行失败（退出码 {completed.returncode}）："
                f"{detail[-800:] or '没有可用错误信息'}"
            )
        try:
            parsed = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Codex 未生成有效的结构化形式化结果") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Codex 形式化结果必须是 JSON object")
        return validate_formalization_result(parsed)
