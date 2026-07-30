from __future__ import annotations

import io
import json
from typing import Any

import numpy as np
import pandas as pd

from quant_lab.application.ports import ArtifactStore
from quant_lab.domain.errors import NotFoundError
from quant_lab.domain.repositories import ProductRepository


class RunBundleEquityReader:
    """Read registered equity artifacts without accepting caller-supplied paths."""

    def __init__(
        self, repository: ProductRepository, artifacts: ArtifactStore
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts

    def read(self, *, bundle_id: str, max_points: int = 800) -> dict[str, Any]:
        if not 50 <= max_points <= 2_000:
            raise ValueError("max_points must be between 50 and 2000")
        report = next(
            (item for item in self.repository.list_reports() if item.id == bundle_id),
            None,
        )
        if report is None:
            raise NotFoundError(f"run bundle not found: {bundle_id}")
        run_id = str(report.summary.get("run_id", "")).strip()
        if not run_id:
            return self._unavailable(bundle_id, "该研究结果没有登记运行标识，无法定位资金曲线。")
        manifest_key = f"experiments/runs/{run_id}/manifest.json"
        if not self.artifacts.exists(manifest_key):
            return self._unavailable(bundle_id, "运行清单不存在，资金曲线不可用。")
        manifest = json.loads(self.artifacts.get(manifest_key))
        equity_keys = [
            str(item.get("artifact_key"))
            for item in manifest.get("outputs", [])
            if isinstance(item, dict)
            and str(item.get("artifact_key", "")).startswith(
                f"experiments/runs/{run_id}/"
            )
            and "equity" in str(item.get("artifact_key", "")).lower()
            and str(item.get("artifact_key", "")).endswith(".parquet")
        ]
        if not equity_keys:
            return self._unavailable(
                bundle_id,
                "该结果未保留资金曲线；当前只展示参数指标，不伪造 Trial 曲线。",
            )

        series: list[dict[str, Any]] = []
        for index, artifact_key in enumerate(equity_keys[:4]):
            if not self.artifacts.exists(artifact_key):
                continue
            frame = pd.read_parquet(io.BytesIO(self.artifacts.get(artifact_key)))
            normalized = _normalize_equity_frame(frame, max_points=max_points)
            for split, points in normalized.items():
                series.append(
                    {
                        "series_id": f"{bundle_id}:{index}:{split}",
                        "label": _series_label(report.report_type, split, index),
                        "kind": "recorded_equity",
                        "points": points,
                        "source_artifact_key": artifact_key,
                        "evidence_mode": "research",
                    }
                )
        if not series:
            return self._unavailable(bundle_id, "资金曲线文件存在，但列结构无法识别。")
        return {
            "available": True,
            "bundle_id": bundle_id,
            "series": series,
            "limitations": [
                "曲线来自已登记的项目相对 Parquet Artifact，并已确定性降采样。",
                "大多数参数 Trial 未单独保留资金曲线，因此不会伪造 Top Trial 曲线。",
                "图表序列不写入 SQLite 或审计事件。",
            ],
        }

    @staticmethod
    def _unavailable(bundle_id: str, reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "bundle_id": bundle_id,
            "series": [],
            "reason": reason,
            "limitations": [],
        }


def _normalize_equity_frame(
    frame: pd.DataFrame, *, max_points: int
) -> dict[str, list[dict[str, Any]]]:
    timestamp_column = next(
        (name for name in ("timestamp", "date", "time") if name in frame), None
    )
    equity_column = next(
        (
            name
            for name in ("equity", "marked_equity", "closed_equity", "realized_equity")
            if name in frame
        ),
        None,
    )
    if timestamp_column is None or equity_column is None or frame.empty:
        return {}
    working = frame[[timestamp_column, equity_column] + (["split"] if "split" in frame else [])].copy()
    working[timestamp_column] = pd.to_datetime(working[timestamp_column], utc=True)
    working[equity_column] = pd.to_numeric(working[equity_column], errors="coerce")
    working = working.dropna(subset=[timestamp_column, equity_column])
    if working.empty:
        return {}
    if "split" not in working:
        working["split"] = "full"

    result: dict[str, list[dict[str, Any]]] = {}
    for split, group in working.groupby("split", observed=True):
        ordered = group.sort_values(timestamp_column)
        if len(ordered) > max_points:
            indices = np.linspace(0, len(ordered) - 1, max_points, dtype=int)
            ordered = ordered.iloc[np.unique(indices)]
        initial = float(ordered[equity_column].iloc[0])
        if initial <= 0:
            continue
        normalized = ordered[equity_column].astype(float) / initial
        drawdown = normalized / normalized.cummax() - 1.0
        result[str(split)] = [
            {
                "t": timestamp.isoformat(),
                "normalized_equity": float(equity),
                "drawdown": float(dd),
            }
            for timestamp, equity, dd in zip(
                ordered[timestamp_column], normalized, drawdown, strict=True
            )
        ]
    return result


def _series_label(report_type: str, split: str, index: int) -> str:
    report_label = {
        "baseline_backtest": "冻结基准",
        "candidate_smoke": "候选试跑",
        "candidate_fast_screen": "候选快速初筛",
    }.get(report_type, "研究结果")
    split_label = {"train": "训练", "validation": "验证", "full": "全区间"}.get(
        split, split
    )
    suffix = f" {index + 1}" if index else ""
    return f"{report_label}{suffix} · {split_label}"
