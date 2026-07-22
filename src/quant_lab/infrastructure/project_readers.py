from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _git_value(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class ProjectStatusReader:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def read(self, *, provider_status: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": "Quant Research Lab",
            "mode": "local_research_only",
            "product_stage": "phase_0_foundation",
            "git": {
                "branch": _git_value(self.root, "branch", "--show-current"),
                "revision": _git_value(self.root, "rev-parse", "HEAD"),
            },
            "safety": {
                "live_trading_enabled": False,
                "trade_api_available": False,
                "production_promotion_available": False,
                "arbitrary_shell_available": False,
            },
            "ai_provider": provider_status,
            "market": {
                "requested_primary": "okx",
                "effective_data_source": "binance_official_archive",
                "instrument": "ETH/USDT:USDT perpetual",
                "timezone": "UTC",
            },
        }


class AgentManifestReader:
    """Read and validate the fixed project Agent capability contract."""

    POLICY_ID = "capability_gated_modern_models_only"
    REQUIRED_CAPABILITIES = (
        "native_tool_calling",
        "json_schema_structured_output",
        "multi_turn_tool_results",
        "instruction_hierarchy",
    )
    REQUIRED_FORBIDDEN_MODES = {
        "chat_only",
        "prompt_simulated_tools",
        "free_text_json_repair",
        "silent_weak_model_fallback",
    }
    REQUIRED_SECRET_FORBIDDEN_LOCATIONS = {
        "browser_persistent_storage",
        "git",
        "project_config",
        "sqlite",
        "logs",
    }

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.manifest_path = self.root / "configs" / "agents" / "agent-manifest.json"

    def read(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            return {
                "available": False,
                "reason": "agent manifest not found",
                "provider_configured": False,
            }
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("top-level manifest must be an object")
            self._validate(manifest)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return {
                "available": False,
                "reason": f"agent manifest invalid: {exc}",
                "provider_configured": False,
            }
        return {
            "available": True,
            "provider_configured": False,
            **manifest,
        }

    def summary(self) -> dict[str, Any]:
        manifest = self.read()
        if not manifest["available"]:
            return manifest
        capabilities = manifest["minimum_capabilities"]
        return {
            "available": True,
            "policy_id": manifest["policy_id"],
            "minimum_context_tokens": capabilities["minimum_context_tokens"],
            "weak_model_fallback_allowed": False,
            "provider_configured": False,
        }

    def _validate(self, manifest: dict[str, Any]) -> None:
        if manifest.get("schema_version") != 1:
            raise ValueError("unsupported schema_version")
        if manifest.get("policy_id") != self.POLICY_ID:
            raise ValueError("unsupported policy_id")
        capabilities = manifest.get("minimum_capabilities")
        if not isinstance(capabilities, dict):
            raise ValueError("minimum_capabilities is required")
        for name in self.REQUIRED_CAPABILITIES:
            if capabilities.get(name) is not True:
                raise ValueError(f"minimum capability must be true: {name}")
        if capabilities.get("minimum_context_tokens", 0) < 32768:
            raise ValueError("minimum_context_tokens must be at least 32768")
        if set(capabilities.get("languages", ())) < {"zh-CN", "en"}:
            raise ValueError("Chinese and English support is required")
        if (
            set(manifest.get("forbidden_compatibility_modes", ()))
            < self.REQUIRED_FORBIDDEN_MODES
        ):
            raise ValueError("forbidden compatibility modes are incomplete")
        secret_policy = manifest.get("secret_policy")
        if not isinstance(secret_policy, dict):
            raise ValueError("secret_policy is required")
        if (
            set(secret_policy.get("forbidden_locations", ()))
            < self.REQUIRED_SECRET_FORBIDDEN_LOCATIONS
        ):
            raise ValueError("secret forbidden locations are incomplete")


class DataSummaryReader:
    """Read only committed summaries; never accepts a caller-supplied path."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.summary_path = self.root / "data" / "catalog" / "catalog_summary.json"
        annual_v2 = (
            self.root
            / "data"
            / "manifests"
            / "binance_ethusdt_perpetual_20250720_20260720_v2.json"
        )
        annual_v1 = (
            self.root
            / "data"
            / "manifests"
            / "binance_ethusdt_perpetual_20250720_20260720.json"
        )
        stage1 = (
            self.root
            / "data"
            / "manifests"
            / "binance_ethusdt_perpetual_20260421_20260720.json"
        )
        self.manifest_path = (
            annual_v2
            if annual_v2.is_file()
            else annual_v1
            if annual_v1.is_file()
            else stage1
        )

    def read(self) -> dict[str, Any]:
        if not self.summary_path.is_file() or not self.manifest_path.is_file():
            return {"available": False, "reason": "committed data summary not found"}
        summary = json.loads(self.summary_path.read_text(encoding="utf-8"))
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        gaps = []
        for dataset in manifest.get("processed_datasets", []):
            missing = dataset.get("quality", {}).get("missing_intervals", 0)
            if missing:
                gaps.append(
                    {
                        "dataset": dataset.get("dataset"),
                        "timeframe": dataset.get("timeframe"),
                        "missing_intervals": missing,
                    }
                )
        return {
            "available": True,
            "market_profile": manifest["market_profile"],
            "source": manifest["source"],
            "range": manifest["range"],
            "symbol": manifest["symbol"],
            "data_version": manifest["data_version"],
            "cost_model": manifest["cost_model"],
            "datasets": summary,
            "quality_gaps": gaps,
            "research_limit": (
                "One complete UTC year improves regime coverage but still does not "
                "demonstrate long-term profitability or a repeatable full market cycle."
            ),
        }
