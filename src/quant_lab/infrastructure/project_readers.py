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


class DataSummaryReader:
    """Read only committed summaries; never accepts a caller-supplied path."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.summary_path = self.root / "data" / "catalog" / "catalog_summary.json"
        self.manifest_path = (
            self.root
            / "data"
            / "manifests"
            / "binance_ethusdt_perpetual_20260421_20260720.json"
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
            "cost_model": manifest["cost_model"],
            "datasets": summary,
            "quality_gaps": gaps,
            "research_limit": (
                "90 complete UTC days support engineering validation and initial "
                "screening only; they do not demonstrate long-term profitability."
            ),
        }
