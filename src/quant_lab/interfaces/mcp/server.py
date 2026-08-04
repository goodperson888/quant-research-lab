from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from quant_lab.paths import app_database_path, project_root

from .gateway import McpResearchGateway


SERVER_INSTRUCTIONS = """
你连接的是 Quant Research Lab 本地量化研究工具，不是实盘交易机器人。

标准顺序：
1. 使用 start_strategy_research 保存用户原始策略；
2. 先在对话中解释歧义，再用 propose_strategy_formalization 保存结构化提案；
3. 只有用户明确确认时，调用 confirm_strategy_formalization；
4. 冻结 Baseline 需要第二次独立明确确认；
5. 研究到 Viability 需要 exact subject 的第三次明确授权；
6. 参数搜索、最终保留测试、dry-run、实盘和 Git 不包含在上述授权中。

不得声称回测已运行，除非 get_job 或 get_research_session 返回真实完成证据。
不得承诺盈利，不得尝试任意 Shell、密钥、实盘交易或自动生产晋升。
""".strip()


def create_mcp_server(gateway: McpResearchGateway | None = None) -> FastMCP:
    research = gateway or McpResearchGateway()
    server = FastMCP(
        "Quant Research Lab",
        instructions=SERVER_INSTRUCTIONS,
        log_level="WARNING",
    )
    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    safe_write = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(
        title="查看 Quant Lab 状态",
        description="查看本地数据位置、许可证、本地 AI 权限和不可突破的安全边界。",
        annotations=read_only,
    )
    def quant_lab_status() -> dict[str, Any]:
        return research.status()

    @server.tool(
        title="列出研究会话",
        description="列出最近的研究会话，不修改任何策略或研究状态。",
        annotations=read_only,
    )
    def list_research_sessions(limit: int = 20) -> dict[str, Any]:
        return research.list_research_sessions(limit=limit)

    @server.tool(
        title="读取研究会话",
        description=(
            "读取一个会话的策略草稿、版本、任务、预算、AgentRun 和最新停止原因。"
        ),
        annotations=read_only,
    )
    def get_research_session(session_id: str) -> dict[str, Any]:
        return research.get_research_session(session_id=session_id)

    @server.tool(
        title="读取后台任务",
        description="读取真实 Job 状态和日志；不得从聊天文字推测任务已经完成。",
        annotations=read_only,
    )
    def get_job(job_id: str) -> dict[str, Any]:
        return research.get_job(job_id=job_id)

    @server.tool(
        title="开始策略研究",
        description=(
            "保存用户原始策略并创建独立研究会话。该工具不会自动形式化、"
            "冻结 Baseline 或启动回测。"
        ),
        annotations=safe_write,
    )
    def start_strategy_research(
        title: str,
        strategy_text: str,
        source_type: str = "natural_language",
        source_name: str | None = None,
    ) -> dict[str, Any]:
        return research.start_strategy_research(
            title=title,
            strategy_text=strategy_text,
            source_type=source_type,
            source_name=source_name,
        )

    @server.tool(
        title="提交结构化策略提案",
        description=(
            "保存 AI 生成的结构化规则和歧义，状态停在等待用户确认；"
            "该工具不能替用户确认。"
        ),
        annotations=safe_write,
    )
    def propose_strategy_formalization(
        session_id: str,
        draft_id: str,
        structured_content: dict[str, Any],
        assistant_message: str,
    ) -> dict[str, Any]:
        return research.propose_strategy_formalization(
            session_id=session_id,
            draft_id=draft_id,
            structured_content=structured_content,
            assistant_message=assistant_message,
        )

    @server.tool(
        title="确认结构化策略",
        description=(
            "仅在用户已经明确确认当前 Draft 的结构化规则时调用。"
            "user_confirmed=false 会被后端拒绝。"
        ),
        annotations=safe_write,
    )
    def confirm_strategy_formalization(
        session_id: str,
        draft_id: str,
        user_confirmed: bool,
    ) -> dict[str, Any]:
        return research.confirm_strategy_formalization(
            session_id=session_id,
            draft_id=draft_id,
            user_confirmed=user_confirmed,
        )

    @server.tool(
        title="冻结策略 Baseline",
        description=(
            "按用户第二次独立明确批准冻结不可覆盖的 Baseline v0；"
            "不会自动启动回测。"
        ),
        annotations=safe_write,
    )
    def freeze_strategy_baseline(
        session_id: str,
        draft_id: str,
        user_confirmed: bool,
    ) -> dict[str, Any]:
        return research.freeze_strategy_baseline(
            session_id=session_id,
            draft_id=draft_id,
            user_confirmed=user_confirmed,
        )

    @server.tool(
        title="授权研究到 Viability",
        description=(
            "按 exact Baseline subject 创建一次有预算授权并排队确定性研究。"
            "不包含参数搜索、最终保留测试、dry-run、实盘或 Git。"
        ),
        annotations=safe_write,
    )
    def authorize_research_to_viability(
        session_id: str,
        baseline_version_id: str,
        user_confirmed: bool,
        include_failure_diagnostics: bool = True,
        max_time_minutes: int | None = None,
    ) -> dict[str, Any]:
        return research.authorize_research_to_viability(
            session_id=session_id,
            baseline_version_id=baseline_version_id,
            user_confirmed=user_confirmed,
            include_failure_diagnostics=include_failure_diagnostics,
            max_time_minutes=max_time_minutes,
        )

    @server.prompt(
        title="规范研究一个策略",
        description="返回 Quant Lab 的有界、需要人工门禁的标准研究提示。",
    )
    def research_strategy_workflow(strategy_text: str) -> str:
        return (
            f"{SERVER_INSTRUCTIONS}\n\n"
            "请先复述下面策略的经济逻辑，列出无法确定的交易语义，再开始建档：\n\n"
            f"{strategy_text}"
        )

    @server.resource(
        "quant-lab://research-workflow",
        title="Quant Lab 标准研究边界",
        description="供本地 AI 读取的研究顺序、审批边界和禁止能力。",
        mime_type="text/markdown",
    )
    def research_workflow_resource() -> str:
        return f"# Quant Lab 标准研究边界\n\n{SERVER_INSTRUCTIONS}\n"

    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant-lab-mcp")
    parser.add_argument("--root")
    parser.add_argument("--database")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else project_root()
    database = (
        Path(args.database).expanduser().resolve()
        if args.database
        else app_database_path()
    )
    create_mcp_server(
        McpResearchGateway(root=root, database_path=database)
    ).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
