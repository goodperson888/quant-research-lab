from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import sys

import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from quant_lab.domain.errors import ApprovalRequiredError
from quant_lab.infrastructure.offline_licensing import OfflineLicenseService
from quant_lab.interfaces.api.app import create_app
from quant_lab.interfaces.mcp.config import mcp_connection_info
from quant_lab.interfaces.mcp.gateway import McpGatewayError, McpResearchGateway
from quant_lab.interfaces.mcp.server import create_mcp_server


ROOT = Path(__file__).resolve().parents[1]


def build_mcp_root(tmp_path: Path) -> Path:
    root = tmp_path / "quant-lab-home"
    for relative in (
        "configs/research_authorizations",
        "configs/research_budgets",
    ):
        shutil.copytree(ROOT / relative, root / relative)
    return root


def test_mcp_direct_strategy_flow_is_audited_and_bounded(tmp_path: Path) -> None:
    root = build_mcp_root(tmp_path)
    gateway = McpResearchGateway(
        root=root,
        database_path=root / "runtime/app/quant_lab.sqlite3",
    )

    started = gateway.start_strategy_research(
        title="ETH 15m EMA strategy",
        strategy_text="EMA20 crosses EMA60, then enter long with ATR stop.",
    )
    session_id = started["session"]["id"]
    draft_id = started["draft"]["id"]
    assert started["draft"]["status"] == "draft"
    assert started["agent_run"]["status"] == "waiting_approval"

    proposed = gateway.propose_strategy_formalization(
        session_id=session_id,
        draft_id=draft_id,
        structured_content={
            "name": "EMA cross",
            "market_profile": "crypto_perpetual",
            "timeframe": "15m",
            "entry": {"rule": "EMA20 crosses above EMA60 at bar close"},
            "exit": {"stop": "1 ATR", "take_profit": "2 ATR"},
            "ambiguities": ["position sizing is not specified"],
        },
        assistant_message="已形成结构化提案，仓位大小仍需确认。",
    )
    assert proposed["draft"]["structured_content"]["name"] == "EMA cross"
    assert proposed["agent_run"]["status"] == "waiting_approval"

    with pytest.raises(ApprovalRequiredError):
        gateway.confirm_strategy_formalization(
            session_id=session_id,
            draft_id=draft_id,
            user_confirmed=False,
        )

    confirmed = gateway.confirm_strategy_formalization(
        session_id=session_id,
        draft_id=draft_id,
        user_confirmed=True,
    )
    assert confirmed["draft"]["status"] == "awaiting_confirmation"

    frozen = gateway.freeze_strategy_baseline(
        session_id=session_id,
        draft_id=draft_id,
        user_confirmed=True,
    )
    baseline_id = frozen["baseline"]["id"]
    assert frozen["baseline"]["immutable"] is True

    authorized = gateway.authorize_research_to_viability(
        session_id=session_id,
        baseline_version_id=baseline_id,
        user_confirmed=True,
    )
    assert authorized["job"]["job_type"] == "pipeline_execution"
    assert authorized["job"]["status"] == "queued"
    assert authorized["authorization"]["locked_test_allowed"] is False
    assert "最终保留测试" in authorized["not_authorized"]

    context = gateway.get_research_session(session_id=session_id)
    assert context["session"]["id"] == session_id
    assert context["strategy_versions"][0]["id"] == baseline_id
    assert context["jobs"][0]["id"] == authorized["job"]["id"]
    tool_names = {
        event.payload.get("tool_name")
        for event in gateway.service.list_audit_events(limit=100)
        if event.event_type == "tool_call.recorded"
    }
    assert {
        "intake_strategy",
        "formalize_strategy",
        "freeze_baseline",
        "create_research_authorization",
        "run_authorized_pipeline",
    } <= tool_names


def test_mcp_commercial_write_requires_active_local_ai_license(
    tmp_path: Path,
) -> None:
    root = build_mcp_root(tmp_path)
    license_service = OfflineLicenseService(
        root,
        enforcement_mode="commercial_required",
        public_key_path=root / "missing-public-key.pem",
    )
    gateway = McpResearchGateway(
        root=root,
        database_path=root / "runtime/app/quant_lab.sqlite3",
        license_service=license_service,
    )

    assert gateway.status()["write_available"] is False
    with pytest.raises(McpGatewayError, match="许可证公钥"):
        gateway.start_strategy_research(
            title="blocked",
            strategy_text="must not be written",
        )
    assert gateway.list_research_sessions()["count"] == 0


def test_mcp_server_exposes_only_bounded_research_tools(tmp_path: Path) -> None:
    root = build_mcp_root(tmp_path)
    gateway = McpResearchGateway(
        root=root,
        database_path=root / "runtime/app/quant_lab.sqlite3",
    )
    tools = asyncio.run(create_mcp_server(gateway).list_tools())
    names = {tool.name for tool in tools}

    assert names == {
        "quant_lab_status",
        "list_research_sessions",
        "get_research_session",
        "get_job",
        "start_strategy_research",
        "propose_strategy_formalization",
        "confirm_strategy_formalization",
        "freeze_strategy_baseline",
        "authorize_research_to_viability",
    }
    assert all(
        forbidden not in name
        for name in names
        for forbidden in ("shell", "trade", "credential", "secret")
    )


def test_api_returns_copyable_mcp_connection_without_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = tmp_path / "QuantResearchLab"
    command.write_text("binary", encoding="utf-8")
    monkeypatch.setenv("QUANT_LAB_MCP_COMMAND", str(command))
    monkeypatch.setenv(
        "QUANT_LAB_MCP_ARGS_JSON",
        json.dumps(["--role", "mcp", "--data-home", str(tmp_path)]),
    )
    app = create_app(
        root=tmp_path,
        database_path=tmp_path / "runtime/app/api.sqlite3",
    )
    client = TestClient(app)

    response = client.get("/api/agent/mcp-connection")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["transport"] == "stdio"
    assert "codex mcp add quant-research-lab" in body["codex"]["install_command"]
    assert body["claude"]["config"]["mcpServers"]["quant-research-lab"][
        "command"
    ] == str(command)
    assert "api_key" not in response.text.lower()
    agent = client.get("/api/agent/status").json()
    assert agent["mcp_direct"]["available"] is True


def test_mcp_connection_info_uses_development_python(tmp_path: Path) -> None:
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("python", encoding="utf-8")

    info = mcp_connection_info(tmp_path)

    assert info["command"] == str(python)
    assert info["args"] == ["-m", "quant_lab.interfaces.mcp.server"]


def test_mcp_stdio_transport_initializes_and_lists_tools(tmp_path: Path) -> None:
    root = build_mcp_root(tmp_path)

    async def scenario() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "quant_lab.interfaces.mcp.server",
                "--root",
                str(root),
                "--database",
                str(root / "runtime/app/quant_lab.sqlite3"),
            ],
            cwd=ROOT,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert "start_strategy_research" in {
                    tool.name for tool in tools.tools
                }
                status = await session.call_tool("quant_lab_status", {})
                assert status.isError is False

    asyncio.run(scenario())
