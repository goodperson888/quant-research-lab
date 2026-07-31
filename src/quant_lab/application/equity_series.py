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

    def read(
        self,
        *,
        bundle_id: str,
        max_points: int = 800,
        market_timeframe: str = "1h",
    ) -> dict[str, Any]:
        if not 50 <= max_points <= 2_000:
            raise ValueError("max_points must be between 50 and 2000")
        if market_timeframe not in {"5m", "15m", "1h", "4h"}:
            raise ValueError("market_timeframe must be one of 5m, 15m, 1h, 4h")
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
        trade_keys = [
            str(item.get("artifact_key"))
            for item in manifest.get("outputs", [])
            if isinstance(item, dict)
            and str(item.get("artifact_key", "")).startswith(
                f"experiments/runs/{run_id}/"
            )
            and "trade" in str(item.get("artifact_key", "")).lower()
            and str(item.get("artifact_key", "")).endswith(".parquet")
        ]
        if not equity_keys:
            return self._unavailable(
                bundle_id,
                "该结果未保留资金曲线；当前只展示参数指标，不伪造 Trial 曲线。",
            )

        series: list[dict[str, Any]] = []
        has_full_period_overview = False
        for index, artifact_key in enumerate(equity_keys[:4]):
            if not self.artifacts.exists(artifact_key):
                continue
            frame = pd.read_parquet(io.BytesIO(self.artifacts.get(artifact_key)))
            normalized = _normalize_equity_frame(frame, max_points=max_points)
            overview_points = _combine_equity_splits(
                normalized,
                max_points=max_points,
            )
            if overview_points:
                has_full_period_overview = True
                series.append(
                    {
                        "series_id": f"{bundle_id}:{index}:full_overview",
                        "label": _series_label(
                            report.report_type,
                            "full_overview",
                            index,
                        ),
                        "kind": "derived_full_period_overview",
                        "points": overview_points,
                        "source_artifact_key": artifact_key,
                        "evidence_mode": "derived_from_recorded_equity",
                    }
                )
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
            market_timeframe=market_timeframe,
        )
        available_market_timeframes = (
            market_series.pop("available_timeframes")
            if market_series is not None
            else self._available_market_timeframes(manifest)
        )
        trades, trade_source_keys = self._read_trades(
            trade_keys=trade_keys,
            max_trades=2_000,
        )
        limitations = [
            "曲线来自已登记的项目相对 Parquet Artifact，并已确定性降采样。",
            "大多数参数 Trial 未单独保留资金曲线，因此不会伪造 Top Trial 曲线。",
            "图表序列不写入 SQLite 或审计事件。",
        ]
        if has_full_period_overview:
            limitations.append(
                "全周期概览按时间顺序衔接训练与验证资金曲线；"
                "验证段仍独立归一化评估，概览只用于观察完整研究周期。"
            )
        if market_series is not None:
            limitations.append(
                "行情对照使用本次运行登记数据版本中的 "
                f"{_timeframe_name(market_series['source_timeframe'])} "
                "ETHUSDT 永续 OHLCV。"
            )
            if market_series["aggregated"]:
                limitations.append(
                    "为了控制浏览器负载，展示K线由所选原始周期确定性聚合；"
                    "开高低收和成交量均按K线规则合并。"
                )
        if trades:
            limitations.append(
                "买卖标记来自本次运行登记的逐笔交易 Artifact；价格优先使用实际执行价格。"
            )
        return {
            "available": True,
            "bundle_id": bundle_id,
            "series": series,
            "market_series": market_series,
            "market_reason": market_reason,
            "available_market_timeframes": available_market_timeframes,
            "trades": trades,
            "trade_source_artifact_keys": trade_source_keys,
            "limitations": limitations,
        }

    def read_market_window(
        self,
        *,
        bundle_id: str,
        market_timeframe: str,
        center_time: str,
        bars: int = 360,
    ) -> dict[str, Any]:
        if market_timeframe not in {"5m", "15m", "1h", "4h"}:
            raise ValueError("market_timeframe must be one of 5m, 15m, 1h, 4h")
        if not 120 <= bars <= 800:
            raise ValueError("bars must be between 120 and 800")
        report = next(
            (item for item in self.repository.list_reports() if item.id == bundle_id),
            None,
        )
        if report is None:
            raise NotFoundError(f"run bundle not found: {bundle_id}")
        run_id = str(report.summary.get("run_id", "")).strip()
        if not run_id:
            return self._unavailable_market_window(
                bundle_id,
                "该研究结果没有登记运行标识，无法定位局部行情。",
            )
        manifest_key = f"experiments/runs/{run_id}/manifest.json"
        if not self.artifacts.exists(manifest_key):
            return self._unavailable_market_window(
                bundle_id,
                "运行清单不存在，无法定位局部行情。",
            )
        manifest = json.loads(self.artifacts.get(manifest_key))
        data_manifest = manifest.get("data_manifest")
        data_manifest_key = (
            str(data_manifest.get("artifact_key", "")).strip()
            if isinstance(data_manifest, dict)
            else ""
        )
        if not data_manifest_key or not self.artifacts.exists(data_manifest_key):
            return self._unavailable_market_window(
                bundle_id,
                "本次运行没有可读取的数据清单。",
            )
        market_manifest = json.loads(self.artifacts.get(data_manifest_key))
        dataset = next(
            (
                item
                for item in market_manifest.get("processed_datasets", [])
                if isinstance(item, dict)
                and item.get("dataset") == "futures_ohlcv"
                and item.get("timeframe") == market_timeframe
            ),
            None,
        )
        if dataset is None:
            return self._unavailable_market_window(
                bundle_id,
                f"数据清单未登记 {_timeframe_name(market_timeframe)} 永续行情。",
            )
        center = pd.Timestamp(center_time)
        center = (
            center.tz_localize("UTC")
            if center.tzinfo is None
            else center.tz_convert("UTC")
        )
        interval = pd.Timedelta(_timeframe_delta(market_timeframe))
        coarse_start = center - interval * bars
        coarse_end = center + interval * bars
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
            frame = pd.read_parquet(io.BytesIO(self.artifacts.get(artifact_key)))
            required_columns = {"timestamp", "open", "high", "low", "close"}
            if not required_columns.issubset(frame.columns):
                continue
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            relevant = frame.loc[
                (frame["timestamp"] >= coarse_start)
                & (frame["timestamp"] <= coarse_end),
                [
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    *(["volume"] if "volume" in frame else []),
                ],
            ]
            if not relevant.empty:
                frames.append(relevant)
                source_keys.append(artifact_key)
        if not frames:
            return self._unavailable_market_window(
                bundle_id,
                "选中交易附近没有匹配的行情K线。",
            )
        combined = (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates("timestamp", keep="last")
            .sort_values("timestamp")
        )
        numeric_columns = ["open", "high", "low", "close"]
        if "volume" in combined:
            numeric_columns.append("volume")
        for column in numeric_columns:
            combined[column] = pd.to_numeric(combined[column], errors="coerce")
        combined = combined.dropna(
            subset=["timestamp", "open", "high", "low", "close"]
        )
        if combined.empty:
            return self._unavailable_market_window(
                bundle_id,
                "局部行情文件存在，但没有有效 OHLC。",
            )
        timestamps = combined["timestamp"].astype("int64").to_numpy()
        center_ns = center.value
        center_index = int(np.searchsorted(timestamps, center_ns, side="left"))
        half = bars // 2
        start_index = max(0, center_index - half)
        end_index = min(len(combined), start_index + bars)
        start_index = max(0, end_index - bars)
        combined = combined.iloc[start_index:end_index]
        symbol = market_manifest.get("symbol", {})
        unified_symbol = (
            str(symbol.get("unified", "ETH/USDT:USDT"))
            if isinstance(symbol, dict)
            else "ETH/USDT:USDT"
        )
        return {
            "available": True,
            "bundle_id": bundle_id,
            "market_series": self._serialize_market_series(
                bundle_id=bundle_id,
                unified_symbol=unified_symbol,
                combined=combined,
                source_keys=source_keys,
                source_timeframe=market_timeframe,
                display_timeframe=f"{_timeframe_name(market_timeframe)}/根",
                aggregated=False,
                windowed=True,
            ),
            "reason": None,
        }

    def _read_market_series(
        self,
        *,
        bundle_id: str,
        manifest: dict[str, Any],
        equity_series: list[dict[str, Any]],
        max_points: int,
        market_timeframe: str,
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

        available_timeframes = self._available_market_timeframes(manifest)
        dataset = next(
            (
                item
                for item in market_manifest.get("processed_datasets", [])
                if isinstance(item, dict)
                and item.get("dataset") == "futures_ohlcv"
                and item.get("timeframe") == market_timeframe
            ),
            None,
        )
        if dataset is None:
            return (
                None,
                f"数据清单未登记 {_timeframe_name(market_timeframe)} 永续行情。",
            )

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
            frame = pd.read_parquet(io.BytesIO(self.artifacts.get(artifact_key)))
            required_columns = {"timestamp", "open", "high", "low", "close"}
            if not required_columns.issubset(frame.columns):
                continue
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            relevant = frame.loc[
                (frame["timestamp"] >= start) & (frame["timestamp"] <= end),
                [
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    *(["volume"] if "volume" in frame else []),
                ],
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
        numeric_columns = ["open", "high", "low", "close"]
        if "volume" in combined:
            numeric_columns.append("volume")
        for column in numeric_columns:
            combined[column] = pd.to_numeric(combined[column], errors="coerce")
        combined = combined.dropna(
            subset=["timestamp", "open", "high", "low", "close"]
        )
        if combined.empty:
            return None, "ETH 行情文件存在，但没有有效 OHLC。"
        source_point_count = len(combined)
        combined = _downsample_ohlcv(combined, max_points=max_points)
        aggregated = len(combined) < source_point_count
        display_timeframe = _display_timeframe(
            source_timeframe=market_timeframe,
            source_point_count=source_point_count,
            display_point_count=len(combined),
        )

        symbol = market_manifest.get("symbol", {})
        unified_symbol = (
            str(symbol.get("unified", "ETH/USDT:USDT"))
            if isinstance(symbol, dict)
            else "ETH/USDT:USDT"
        )
        serialized = self._serialize_market_series(
            bundle_id=bundle_id,
            unified_symbol=unified_symbol,
            combined=combined,
            source_keys=source_keys,
            source_timeframe=market_timeframe,
            display_timeframe=display_timeframe,
            aggregated=aggregated,
            windowed=False,
        )
        serialized["available_timeframes"] = available_timeframes
        return serialized, None

    @staticmethod
    def _serialize_market_series(
        *,
        bundle_id: str,
        unified_symbol: str,
        combined: pd.DataFrame,
        source_keys: list[str],
        source_timeframe: str,
        display_timeframe: str,
        aggregated: bool,
        windowed: bool,
    ) -> dict[str, Any]:
        return {
            "series_id": f"{bundle_id}:market",
            "label": f"{unified_symbol} 行情",
            "kind": "market_price",
            "unit": "quote_price",
            "timeframe": display_timeframe,
            "source_timeframe": source_timeframe,
            "aggregated": aggregated,
            "windowed": windowed,
            "points": [
                {
                    "t": timestamp.isoformat(),
                    "value": float(close),
                }
                for timestamp, close in zip(
                    combined["timestamp"], combined["close"], strict=True
                )
            ],
            "candles": [
                {
                    "t": timestamp.isoformat(),
                    "open": float(open_price),
                    "high": float(high_price),
                    "low": float(low_price),
                    "close": float(close_price),
                    "volume": (
                        float(volume)
                        if volume is not None and np.isfinite(volume)
                        else 0.0
                    ),
                }
                for timestamp, open_price, high_price, low_price, close_price, volume in zip(
                    combined["timestamp"],
                    combined["open"],
                    combined["high"],
                    combined["low"],
                    combined["close"],
                    (
                        combined["volume"]
                        if "volume" in combined
                        else [0.0] * len(combined)
                    ),
                    strict=True,
                )
            ],
            "source_artifact_keys": source_keys,
            "evidence_mode": "market_context",
        }

    def _available_market_timeframes(self, manifest: dict[str, Any]) -> list[str]:
        data_manifest = manifest.get("data_manifest")
        manifest_key = (
            str(data_manifest.get("artifact_key", "")).strip()
            if isinstance(data_manifest, dict)
            else ""
        )
        if not manifest_key or not self.artifacts.exists(manifest_key):
            return []
        try:
            market_manifest = json.loads(self.artifacts.get(manifest_key))
        except (json.JSONDecodeError, OSError):
            return []
        available = {
            str(item.get("timeframe"))
            for item in market_manifest.get("processed_datasets", [])
            if isinstance(item, dict)
            and item.get("dataset") == "futures_ohlcv"
            and item.get("timeframe") in {"5m", "15m", "1h", "4h"}
        }
        order = {"5m": 0, "15m": 1, "1h": 2, "4h": 3}
        return sorted(available, key=order.__getitem__)

    def _read_trades(
        self,
        *,
        trade_keys: list[str],
        max_trades: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        records: list[dict[str, Any]] = []
        source_keys: list[str] = []
        for artifact_key in trade_keys[:12]:
            if not self.artifacts.exists(artifact_key):
                continue
            frame = pd.read_parquet(io.BytesIO(self.artifacts.get(artifact_key)))
            normalized = _normalize_trade_frame(frame, artifact_key=artifact_key)
            if normalized:
                records.extend(normalized)
                source_keys.append(artifact_key)
        records.sort(key=lambda item: (item["entry_time"], item["trade_id"]))
        if len(records) > max_trades:
            records = records[-max_trades:]
        return records, source_keys

    @staticmethod
    def _unavailable(bundle_id: str, reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "bundle_id": bundle_id,
            "series": [],
            "market_series": None,
            "market_reason": None,
            "available_market_timeframes": [],
            "trades": [],
            "trade_source_artifact_keys": [],
            "reason": reason,
            "limitations": [],
        }

    @staticmethod
    def _unavailable_market_window(
        bundle_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "available": False,
            "bundle_id": bundle_id,
            "market_series": None,
            "reason": reason,
        }


def _display_timeframe(
    *,
    source_timeframe: str,
    source_point_count: int,
    display_point_count: int,
) -> str:
    source_minutes = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}[source_timeframe]
    if display_point_count >= source_point_count:
        return f"{_timeframe_name(source_timeframe)}/根"
    display_minutes = (
        source_minutes * source_point_count / max(display_point_count, 1)
    )
    if display_minutes < 60:
        interval = f"{display_minutes:.0f}分钟"
    else:
        interval = f"{display_minutes / 60:.1f}小时"
    return (
        f"约{interval}/根"
        f"（由{_timeframe_name(source_timeframe)}K线聚合）"
    )


def _timeframe_name(timeframe: str) -> str:
    return {
        "5m": "5分钟",
        "15m": "15分钟",
        "1h": "1小时",
        "4h": "4小时",
    }[timeframe]


def _timeframe_delta(timeframe: str) -> str:
    return {
        "5m": "5min",
        "15m": "15min",
        "1h": "1h",
        "4h": "4h",
    }[timeframe]


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
        "full_overview": "全周期概览",
    }.get(split, split)
    suffix = f" {index + 1}" if index else ""
    return f"{report_label}{suffix} · {split_label}"


def _combine_equity_splits(
    normalized: dict[str, list[dict[str, Any]]],
    *,
    max_points: int,
) -> list[dict[str, Any]]:
    ordered_splits = [
        normalized[split]
        for split in ("train", "validation")
        if normalized.get(split)
    ]
    if len(ordered_splits) < 2:
        return []
    combined: list[dict[str, Any]] = []
    current_equity = 1.0
    running_peak = 1.0
    for points in ordered_splits:
        initial = float(points[0]["normalized_equity"])
        if initial <= 0:
            return []
        for point in points:
            equity = current_equity * float(point["normalized_equity"]) / initial
            running_peak = max(running_peak, equity)
            combined.append(
                {
                    "t": str(point["t"]),
                    "normalized_equity": equity,
                    "drawdown": equity / running_peak - 1.0,
                }
            )
        current_equity = float(combined[-1]["normalized_equity"])
    if len(combined) <= max_points:
        return combined
    indices = np.linspace(0, len(combined) - 1, max_points, dtype=int)
    return [combined[index] for index in np.unique(indices)]


def _downsample_ohlcv(frame: pd.DataFrame, *, max_points: int) -> pd.DataFrame:
    if len(frame) <= max_points:
        return frame
    bucket_ids = np.floor(
        np.arange(len(frame), dtype=float) * max_points / len(frame)
    ).astype(int)
    working = frame.copy()
    working["_bucket"] = np.minimum(bucket_ids, max_points - 1)
    aggregations: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in working:
        aggregations["volume"] = "sum"
    return (
        working.groupby("_bucket", sort=True, observed=True)
        .agg(aggregations)
        .reset_index(drop=True)
    )


def _normalize_trade_frame(
    frame: pd.DataFrame,
    *,
    artifact_key: str,
) -> list[dict[str, Any]]:
    if frame.empty or "entry_time" not in frame or "exit_time" not in frame:
        return []
    entry_price_column = _first_column(
        frame,
        "entry_execution_price",
        "entry_price",
        "entry_raw_price",
    )
    exit_price_column = _first_column(
        frame,
        "exit_execution_price",
        "exit_price",
        "exit_raw_price",
    )
    if entry_price_column is None or exit_price_column is None:
        return []
    stop_column = _first_column(
        frame,
        "initial_stop_price",
        "stop_price",
        "stop_price_at_exit",
    )
    take_profit_column = _first_column(frame, "take_profit_price")
    return_column = _first_column(
        frame,
        "net_return",
        "net_return_on_entry_equity",
        "return_on_initial_equity",
    )
    pnl_column = _first_column(frame, "net_pnl")
    split_from_name = next(
        (
            value
            for value in ("validation", "train", "smoke")
            if value in artifact_key.lower()
        ),
        "full",
    )
    working = frame.copy()
    working["entry_time"] = pd.to_datetime(working["entry_time"], utc=True)
    working["exit_time"] = pd.to_datetime(working["exit_time"], utc=True)
    records: list[dict[str, Any]] = []
    for index, row in working.iterrows():
        entry_price = _finite_float(row.get(entry_price_column))
        exit_price = _finite_float(row.get(exit_price_column))
        if (
            pd.isna(row.get("entry_time"))
            or pd.isna(row.get("exit_time"))
            or entry_price is None
            or exit_price is None
        ):
            continue
        side = str(row.get("side", "")).lower()
        if side not in {"long", "short"}:
            side = "unknown"
        raw_trade_id = row.get("trade_id", index + 1)
        artifact_name = artifact_key.rsplit("/", maxsplit=1)[-1].removesuffix(
            ".parquet"
        )
        records.append(
            {
                "trade_id": f"{artifact_name}:{raw_trade_id}",
                "split": str(row.get("split", split_from_name)),
                "side": side,
                "entry_time": row["entry_time"].isoformat(),
                "exit_time": row["exit_time"].isoformat(),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_price": (
                    _finite_float(row.get(stop_column)) if stop_column else None
                ),
                "take_profit_price": (
                    _finite_float(row.get(take_profit_column))
                    if take_profit_column
                    else None
                ),
                "net_return": (
                    _finite_float(row.get(return_column))
                    if return_column
                    else None
                ),
                "net_pnl": (
                    _finite_float(row.get(pnl_column)) if pnl_column else None
                ),
                "exit_reason": str(row.get("exit_reason", "unknown")),
                "source_artifact_key": artifact_key,
            }
        )
    return records


def _first_column(frame: pd.DataFrame, *names: str) -> str | None:
    return next((name for name in names if name in frame), None)


def _finite_float(value: Any) -> float | None:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if np.isfinite(normalized) else None
