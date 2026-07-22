from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from quant_lab.application.baseline_backtest import (
    BacktestSliceResult,
    PriceStructureBaselineBacktester,
)
from quant_lab.application.services import new_id
from quant_lab.domain.models import Job, Report, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository
from quant_lab.runs import _git_revision, _source_tree_digest

from .artifact_store import LocalArtifactStore


class BaselineBacktestRunner:
    """Allowlisted local worker handler for the frozen price-structure baseline."""

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.artifacts = LocalArtifactStore(self.root)

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "intent",
            "session_id",
            "strategy_version_id",
            "config_artifact_key",
            "agent_run_id",
        }
        missing = required - set(payload)
        if missing:
            raise ValueError(f"baseline job payload missing: {sorted(missing)}")
        if payload["intent"] != "baseline_backtest":
            raise ValueError("backtest handler only accepts baseline_backtest intent")
        config_key = validate_artifact_key(str(payload["config_artifact_key"]))
        config = self._load_yaml(config_key)
        self._validate_config(config, payload)
        return config

    def __call__(self, job: Job) -> Mapping[str, Any]:
        config = self.validate_payload(job.payload)
        data_manifest_key = validate_artifact_key(config["data_manifest_key"])
        data_manifest_bytes = self.artifacts.get(data_manifest_key)
        data_manifest = json.loads(data_manifest_bytes)
        self._validate_data_manifest(config, data_manifest)

        ohlcv = self._load_dataset(data_manifest, "futures_ohlcv", "15m")
        funding = self._load_dataset(data_manifest, "funding_rate", None)
        mark = self._load_dataset(data_manifest, "mark_price", "15m")

        costs = config["cost_model"]
        backtester = PriceStructureBaselineBacktester(
            fee_per_side=float(costs["fee_per_side"]),
            slippage_bps_per_side=float(costs["slippage_bps_per_side"]),
            funding_missing_policy=config["funding"]["missing_policy"],
        )

        evaluation = config["evaluation_range"]
        results: dict[str, BacktestSliceResult] = {
            "full": backtester.run(
                label="full",
                ohlcv=ohlcv,
                funding=funding,
                mark=mark,
                start_utc_inclusive=evaluation["start_utc_inclusive"],
                end_utc_exclusive=evaluation["end_utc_exclusive"],
            )
        }
        for label in ("train", "validation", "locked_test"):
            split = config["time_splits"][label]
            results[label] = backtester.run(
                label=label,
                ohlcv=ohlcv,
                funding=funding,
                mark=mark,
                start_utc_inclusive=split["start_utc_inclusive"],
                end_utc_exclusive=split["end_utc_exclusive"],
            )

        run_id = self._run_id(job)
        run_prefix = f"experiments/runs/{run_id}"
        manifest_key = f"{run_prefix}/manifest.json"
        if self.artifacts.exists(manifest_key):
            raise FileExistsError("run artifacts are immutable and already exist")

        output_records: list[dict[str, Any]] = []
        metrics_payload = {
            label: {
                "range": {
                    "start_utc_inclusive": result.start_utc_inclusive,
                    "end_utc_exclusive": result.end_utc_exclusive,
                },
                "metrics": dict(result.metrics),
                "funding": dict(result.funding),
                "execution": dict(result.execution),
            }
            for label, result in results.items()
        }
        output_records.append(
            self._put_json(f"{run_prefix}/metrics.json", metrics_payload)
        )
        output_records.append(
            self._put_parquet(f"{run_prefix}/trades.parquet", results["full"].trades)
        )
        output_records.append(
            self._put_parquet(f"{run_prefix}/equity.parquet", results["full"].equity)
        )

        log_lines = [
            {
                "level": "info",
                "event": "baseline_backtest.started",
                "job_id": job.id,
                "strategy_version_id": config["strategy_version_id"],
            },
            {
                "level": "warning",
                "event": "funding.missing_policy_applied",
                "policy": config["funding"]["missing_policy"],
                "missing_events": results["full"].funding["missing_events_in_slice"],
                "zero_funding_fallback_used": False,
            },
            {
                "level": "info",
                "event": "locked_test.inspected",
                "inspection_count": 1,
                "contaminated": False,
            },
            {
                "level": "info",
                "event": "baseline_backtest.completed",
                "trade_count": results["full"].metrics["trade_count"],
            },
        ]
        log_bytes = (
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in log_lines)
        ).encode("utf-8")
        output_records.append(self._put_bytes(f"{run_prefix}/job.log.jsonl", log_bytes))

        report_key = f"reports/backtests/{run_id}.md"
        report_bytes = self._build_report(run_id, config, data_manifest, results).encode(
            "utf-8"
        )
        output_records.append(self._put_bytes(report_key, report_bytes))

        created_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "run_type": "baseline_backtest",
            "intent": "baseline_backtest",
            "status": "succeeded",
            "created_at": created_at,
            "agent_run_id": job.payload["agent_run_id"],
            "routing": dict(job.payload.get("routing", {})),
            "strategy": {
                "session_id": config["session_id"],
                "strategy_version_id": config["strategy_version_id"],
                "version": 0,
                "immutable_baseline": True,
                "implementation_artifact_key": config["strategy_artifact_key"],
            },
            "market_profile": config["market_profile"],
            "validation_scope": "binance_only",
            "data_manifest": {
                "artifact_key": data_manifest_key,
                "sha256": hashlib.sha256(data_manifest_bytes).hexdigest(),
                "dataset_id": data_manifest["dataset_id"],
                "data_version": data_manifest["data_version"],
            },
            "parameters": dict(config["strategy_parameters"]),
            "cost_model": dict(config["cost_model"]),
            "funding": {
                **dict(config["funding"]),
                "result": dict(results["full"].funding),
            },
            "time_splits": dict(config["time_splits"]),
            "locked_test": {
                "inspection_count": 1,
                "contaminated": False,
                "rule": "Any strategy modification after this report contaminates this locked period.",
            },
            "random_seed": 20260721,
            "code_version": _source_tree_digest(self.root),
            "git_revision": _git_revision(self.root),
            "engine": "quant_lab_native_event_driven_v1",
            "results": metrics_payload,
            "outputs": output_records,
            "limitations": list(config["limitations"]),
            "claims": {
                "long_term_profitability_proven": False,
                "okx_validated": False,
                "production_ready": False,
            },
        }
        manifest_record = self._put_json(manifest_key, manifest)
        output_records.append(manifest_record)

        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type="baseline_backtest",
                artifact_key=report_key,
                summary={
                    "run_id": run_id,
                    "trade_count": results["full"].metrics["trade_count"],
                    "total_return": results["full"].metrics["total_return"],
                    "max_drawdown": results["full"].metrics["max_drawdown"],
                    "validation_scope": "binance_only",
                    "funding_imputed": True,
                },
                created_at=created_at,
            )
        )
        return {
            "run_id": run_id,
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [record["artifact_key"] for record in output_records],
            "summary": metrics_payload["full"],
        }

    def _validate_config(
        self, config: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> None:
        required = {
            "schema_version",
            "session_id",
            "strategy_version_id",
            "strategy_artifact_key",
            "market_profile",
            "data_manifest_key",
            "evaluation_range",
            "time_splits",
            "strategy_parameters",
            "cost_model",
            "funding",
            "limitations",
        }
        missing = required - set(config)
        if missing:
            raise ValueError(f"baseline config missing: {sorted(missing)}")
        if config["session_id"] != payload["session_id"]:
            raise ValueError("job session does not match baseline config")
        if config["strategy_version_id"] != payload["strategy_version_id"]:
            raise ValueError("job strategy version does not match baseline config")
        version = self.repository.get_strategy_version(config["strategy_version_id"])
        if version.status != "baseline" or not version.immutable or version.version != 0:
            raise ValueError("baseline backtest requires immutable strategy version v0")
        if config["market_profile"] != "crypto_perpetual.binance.eth":
            raise ValueError("this engineering baseline is scoped to Binance only")
        validate_artifact_key(config["strategy_artifact_key"])
        validate_artifact_key(config["data_manifest_key"])
        if not self.artifacts.exists(config["strategy_artifact_key"]):
            raise ValueError("strategy implementation artifact does not exist")
        if config["funding"].get("missing_policy") != "adverse_p99_abs_observed":
            raise ValueError("funding gaps must use the declared conservative policy")
        if config["funding"].get("zero_funding_fallback_allowed") is not False:
            raise ValueError("zero funding fallback must be explicitly forbidden")

        expected = config["evaluation_range"]
        splits = config["time_splits"]
        for label in ("train", "validation", "locked_test"):
            if label not in splits:
                raise ValueError(f"time split missing: {label}")
        boundaries = [
            splits["train"]["start_utc_inclusive"],
            splits["train"]["end_utc_exclusive"],
            splits["validation"]["end_utc_exclusive"],
            splits["locked_test"]["end_utc_exclusive"],
        ]
        if splits["validation"]["start_utc_inclusive"] != boundaries[1]:
            raise ValueError("validation split must follow train without overlap")
        if splits["locked_test"]["start_utc_inclusive"] != boundaries[2]:
            raise ValueError("locked test must follow validation without overlap")
        if boundaries[0] != expected["start_utc_inclusive"] or boundaries[-1] != expected["end_utc_exclusive"]:
            raise ValueError("time splits must cover the evaluation range exactly")

    @staticmethod
    def _validate_data_manifest(
        config: Mapping[str, Any], manifest: Mapping[str, Any]
    ) -> None:
        if manifest["market_profile"] != config["market_profile"]:
            raise ValueError("data manifest market profile does not match validation scope")
        if manifest["range"] != config["evaluation_range"]:
            expected = config["evaluation_range"]
            actual = manifest["range"]
            if (
                actual["start_utc_inclusive"] != expected["start_utc_inclusive"]
                or actual["end_utc_exclusive"] != expected["end_utc_exclusive"]
            ):
                raise ValueError("data manifest range does not match evaluation range")

    def _load_yaml(self, artifact_key: str) -> dict[str, Any]:
        loaded = yaml.safe_load(self.artifacts.get(artifact_key))
        if not isinstance(loaded, dict):
            raise ValueError("baseline config must be a YAML mapping")
        return loaded

    def _load_dataset(
        self,
        manifest: Mapping[str, Any],
        dataset: str,
        timeframe: str | None,
    ) -> pd.DataFrame:
        matches = [
            item
            for item in manifest["processed_datasets"]
            if item["dataset"] == dataset
            and (timeframe is None or item.get("timeframe") == timeframe)
        ]
        if len(matches) != 1:
            raise ValueError(f"manifest must contain exactly one {dataset}/{timeframe}")
        frames: list[pd.DataFrame] = []
        for output in matches[0]["outputs"]:
            key = validate_artifact_key(output["path"])
            path = (self.root / key).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise ValueError("dataset output escapes project root") from exc
            content = path.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            if checksum != output["sha256"]:
                raise ValueError(f"dataset checksum mismatch: {key}")
            frames.append(pd.read_parquet(io.BytesIO(content)))
        if not frames:
            raise ValueError(f"dataset has no outputs: {dataset}/{timeframe}")
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _run_id(job: Job) -> str:
        created = pd.Timestamp(job.created_at).tz_convert("UTC")
        return f"{created.strftime('%Y%m%dT%H%M%SZ')}-baseline-{job.id[-8:]}"

    def _put_json(self, artifact_key: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        content = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        return self._put_bytes(artifact_key, content)

    def _put_parquet(self, artifact_key: str, frame: pd.DataFrame) -> dict[str, Any]:
        buffer = io.BytesIO()
        frame.to_parquet(buffer, index=False, compression="zstd")
        return self._put_bytes(artifact_key, buffer.getvalue(), rows=len(frame))

    def _put_bytes(
        self, artifact_key: str, content: bytes, *, rows: int | None = None
    ) -> dict[str, Any]:
        self.artifacts.put(artifact_key, content)
        record: dict[str, Any] = {
            "artifact_key": artifact_key,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if rows is not None:
            record["rows"] = rows
        return record

    @staticmethod
    def _build_report(
        run_id: str,
        config: Mapping[str, Any],
        data_manifest: Mapping[str, Any],
        results: Mapping[str, BacktestSliceResult],
    ) -> str:
        def percent(value: Any) -> str:
            return "—" if value is None else f"{float(value):.2%}"

        lines = [
            "# 价格结构 baseline v0：Binance 工程基准报告",
            "",
            f"- Run：`{run_id}`",
            f"- 冻结策略版本：`{config['strategy_version_id']}`",
            "- 验证范围：仅 Binance ETH/USDT USDT 本位永续，不能传播到 OKX。",
            f"- 数据版本：`{data_manifest['data_version']}`",
            "- 执行周期：15m；杠杆：1；实盘：关闭。",
            "- 定位：90 天工程验证和原样基准，不证明长期盈利。",
            "",
            "## 时间切分与结果",
            "",
            "| 区间 | UTC 范围 | 交易数 | 总收益 | 最大回撤 | 胜率 | Profit Factor |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for label in ("train", "validation", "locked_test", "full"):
            result = results[label]
            metrics = result.metrics
            profit_factor = metrics["profit_factor"]
            lines.append(
                "| {label} | [{start}, {end}) | {trades} | {ret} | {dd} | {win} | {pf} |".format(
                    label=label,
                    start=result.start_utc_inclusive,
                    end=result.end_utc_exclusive,
                    trades=metrics["trade_count"],
                    ret=percent(metrics["total_return"]),
                    dd=percent(metrics["max_drawdown"]),
                    win=percent(metrics["win_rate"]),
                    pf="—" if profit_factor is None else f"{float(profit_factor):.3f}",
                )
            )
        full = results["full"]
        lines.extend(
            [
                "",
                "## 成本与资金费率",
                "",
                f"- Taker 手续费：{config['cost_model']['fee_per_side']:.4%}/边。",
                f"- 滑点：{config['cost_model']['slippage_bps_per_side']} bps/边。",
                f"- 预期资金费率时点：{full.funding['expected_events']}；数据可用：{full.funding['observed_events_available']}；缺失：{full.funding['missing_events_in_slice']}。",
                f"- 缺失政策：`{full.funding['missing_policy']}`，不利代理费率绝对值 {full.funding['adverse_proxy_rate']:.8f}。",
                f"- 持仓期间实际应用资金费率 {full.funding['observed_events_applied_while_position_open']} 次，保守插补 {full.funding['imputed_events_applied_while_position_open']} 次。",
                "- 未使用静默零资金费率。插补结果只能视为保守工程情景，不等于真实历史资金费率。",
                "",
                "## 锁定测试声明",
                "",
                "本报告首次查看最后 10 个完整 UTC 日的锁定测试结果，当前标记为未污染。此后若依据该结果修改策略，必须将该锁定区间标记为污染，并建立新的锁定测试期。",
                "",
                "## 已知限制",
                "",
            ]
        )
        lines.extend(f"- {item}" for item in config["limitations"])
        lines.extend(
            [
                "- 本轮使用项目原生确定性事件回测器；Freqtrade `exchangeInfo` 联网超时问题尚未解决，完整引擎对账仍待进行。",
                "- 交易所最小下单金额、数量/价格精度、杠杆阶梯和强平引擎尚未完整建模。",
                "- 不构成收益承诺、生产晋升依据或实盘授权。",
                "",
                "## 下一步门禁",
                "",
                "先人工核对代表性交易与参考平台信号时点。只有基准代码和逐笔交易可解释后，才允许提出一个单独的改进假设；不得覆盖 baseline v0。",
                "",
            ]
        )
        return "\n".join(lines)
