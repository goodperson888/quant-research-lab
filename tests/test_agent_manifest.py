import ast
import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from quant_lab.infrastructure.project_readers import AgentManifestReader
from quant_lab.interfaces.api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_agent_manifest_enforces_modern_model_and_secret_policy() -> None:
    manifest = AgentManifestReader(ROOT).read()

    assert manifest["available"] is True
    assert manifest["provider_configured"] is False
    assert manifest["policy_id"] == "capability_gated_modern_models_only"
    capabilities = manifest["minimum_capabilities"]
    assert capabilities["native_tool_calling"] is True
    assert capabilities["json_schema_structured_output"] is True
    assert capabilities["multi_turn_tool_results"] is True
    assert capabilities["minimum_context_tokens"] >= 32768
    assert capabilities["instruction_hierarchy"] is True
    assert set(capabilities["languages"]) >= {"zh-CN", "en"}
    assert "silent_weak_model_fallback" in manifest["forbidden_compatibility_modes"]
    assert set(manifest["secret_policy"]["forbidden_locations"]) >= {
        "browser_persistent_storage",
        "git",
        "project_config",
        "sqlite",
        "logs",
    }


def test_missing_agent_manifest_is_explicitly_unavailable(tmp_path: Path) -> None:
    reader = AgentManifestReader(tmp_path)

    assert reader.read() == {
        "available": False,
        "reason": "agent manifest not found",
        "provider_configured": False,
    }
    client = TestClient(
        create_app(root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3")
    )
    response = client.get("/api/agent/manifest")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert client.get("/api/agent/status").json()["model_policy"] == reader.summary()


def test_agent_manifest_api_and_openapi_are_read_only(tmp_path: Path) -> None:
    target = tmp_path / "configs/agents/agent-manifest.json"
    target.parent.mkdir(parents=True)
    shutil.copy(ROOT / "configs/agents/agent-manifest.json", target)
    client = TestClient(
        create_app(root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3")
    )
    response = client.get("/api/agent/manifest")
    assert response.status_code == 200
    assert response.json()["available"] is True
    summary = client.get("/api/agent/status").json()["model_policy"]
    assert summary["weak_model_fallback_allowed"] is False
    assert summary["provider_configured"] is False

    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/api/agent/manifest" in paths
    assert all(
        forbidden not in path.lower()
        for path in paths
        for forbidden in ("/trade", "/live", "/shell", "credential")
    )


def test_core_python_does_not_import_freqtrade() -> None:
    offenders: list[str] = []
    for path in (ROOT / "src/quant_lab").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "freqtrade" or name.startswith("freqtrade.") for name in names):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_agent_manifest_is_valid_json() -> None:
    path = ROOT / "configs/agents/agent-manifest.json"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
