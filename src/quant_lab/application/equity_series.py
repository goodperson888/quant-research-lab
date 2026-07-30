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
        market_series, market_reason = self._read_market_series(
            bundle_id=bundle_id,
            manifest=manifest,
            equity_series=series,
            max_points=max_points,
        )
        limitations = [
            "曲线来自已登记的项目相对 Parquet Artifact，并已确定性降采样。",
            "大多数参数 Trial 未单独保留资金曲线，因此不会伪造 Top Trial 曲线。",
            "图表序列不写入 SQLite 或审计事件。",
        ]
        if market_series is not None:
            limitations.append(
                "行情对照使用本次运行登记数据版本中的 1 小时 ETHUSDT 永续收盘价。"
            )
        return {
            "available": True,
            "bundle_id": bundle_id,
            "series": series,
            "market_series": market_series,
            "market_reason": market_reason,
            "limitations": limitations,
        }

    def _read_market_series(
        self,
        *,
        bundle_id: str,
        manifest: dict[str, Any],
        equity_series: list[dict[str, Any]],
        max_points: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        data_manifest = manifest.get("data_manifest")
        manifest_key = (
            str(data_manifest.get("artifact_key", "")).strip()
            if isinstance(data_manifest, dict)
            else ""
        )
        if not manifest_key or not self.artifacts.exists(manifest_key):
            return None, "本次运行没有可读取的数据清单，无法叠加行情走势。"
        try:
            market_manifest = json.loads(self.artifacts.get(manifest_key))
        except (json.JSONDecodeError, OSError):
            return None, "行情数据清单无法读取。"

        dataset = next(
            (
                item
                for item in market_manifest.get("processed_datasets", [])
                if isinstance(item, dict)
                and item.get("dataset") == "futures_ohlcv"
                and item.get("timeframe") == "1h"
            ),
            None,
        )
        if dataset is None:
            return None, "数据清单未登记 1 小时永续行情。"

        timestamps = [
            pd.Timestamp(point["t"])
            for item in equity_series
            for point in item.get("points", [])
            if point.get("t")
        ]
        if not timestamps:
            return None, "资金曲线没有有效时间范围，无法对齐行情。"
        start = min(timestamps)
        end = max(timestamps)

        frames: list[pd.DataFrame] = []
        source_keys: list[str] = []
        for output in dataset.get("outputs", [])[:48]:
            if not isinstance(output, dict):
                continue
            artifact_key = str(output.get("path", "")).strip()
            if not artifact_key.endswith(".parquet") or not self.artifacts.exists(
                artifact_key
            ):
                continue
            frame = pd.read_parquet(
                io.BytesIO(self.artifacts.get(artifact_key)),
                columns=["timestamp", "close"],
            )
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            relevant = frame.loc[
                (frame["timestamp"] >= start) & (frame["timestamp"] <= end),
                ["timestamp", "close"],
            ]
            if not relevant.empty:
                frames.append(relevant)
                source_keys.append(artifact_key)
        if not frames:
            return None, "资金曲线时间范围内没有匹配的 ETH 行情。"

        combined = (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates("timestamp", keep="last")
            .sort_values("timestamp")
        )
        combined["close"] = pd.to_numeric(combined["close"], errors="coerce")
        combined = combined.dropna(subset=["timestamp", "close"])
        if combined.empty:
            return None, "ETH 行情文件存在，但没有有效收盘价。"
        if len(combined) > max_points:
            indices = np.linspace(0, len(combined) - 1, max_points, dtype=int)
            combined = combined.iloc[np.unique(indices)]

        symbol = market_manifest.get("symbol", {})
        unified_symbol = (
            str(symbol.get("unified", "ETH/USDT:USDT"))
            if isinstance(symbol, dict)
            else "ETH/USDT:USDT"
        )
        return (
            {
                "series_id": f"{bundle_id}:market",
                "label": f"{unified_symbol} 行情",
                "kind": "market_price",
                "unit": "quote_price",
                "points": [
                    {
                        "t": timestamp.isoformat(),
                        "value": float(close),
                    }
                    for timestamp, close in zip(
                        combined["timestamp"], combined["close"], strict=True
                    )
                ],
                "source_artifact_keys": source_keys,
                "evidence_mode": "market_context",
            },
            None,
        )

    @staticmethod
    def _unavailable(bundle_id: str, reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "bundle_id": bundle_id,
            "series": [],
            "market_series": None,
            "market_reason": None,
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
    if report_type == "baseline_backtest":
        report_label = "冻结基准"
    elif "fast_screen" in report_type:
        report_label = "快速初筛"
    elif "smoke" in report_type:
        report_label = "小范围试跑"
    elif "candidate" in report_type:
        report_label = "候选策略"
    else:
        report_label = "研究结果"
    split_label = {
        "train": "训练",
        "validation": "验证",
        "smoke": "试跑区间",
        "full": "全区间",
    }.get(split, split)
    suffix = f" {index + 1}" if index else ""
    return f"{report_label}{suffix} · {split_label}"
