from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from quant_lab.application.services import ResearchApplicationService, new_id
from quant_lab.application.trade_reconciliation import (
    BaselineTradeReconciler,
    reconciliation_json,
)
from quant_lab.domain.models import AuditEvent, Report
from quant_lab.domain.repositories import ProductRepository

from .artifact_store import LocalArtifactStore
from .baseline_backtest_runner import BaselineBacktestRunner


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class TradeReconciliationReportGenerator:
    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.service = ResearchApplicationService(repository)
        self.artifacts = LocalArtifactStore(self.root)

    def generate(self, run_id: str) -> dict[str, Any]:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("run_id contains unsupported path characters")
        manifest_key = f"experiments/runs/{run_id}/manifest.json"
        manifest = json.loads(self.artifacts.get(manifest_key))
        if manifest.get("run_type") != "baseline_backtest":
            raise ValueError("trade reconciliation requires a baseline_backtest run")

        session_id = manifest["strategy"]["session_id"]
        agent_run = self.service.create_agent_run(
            session_id=session_id,
            agent_name="codex",
            mode="guided",
            plan_summary=(
                "Reconcile baseline trades against confirmed pivots, next-open signals, "
                "protective stops, slippage and fee arithmetic without changing strategy."
            ),
        )

        try:
            data_manifest = json.loads(
                self.artifacts.get(manifest["data_manifest"]["artifact_key"])
            )
            loader = BaselineBacktestRunner(self.root, self.repository)
            bars = loader._load_dataset(data_manifest, "futures_ohlcv", "15m")
            trades = pd.read_parquet(
                self.root / f"experiments/runs/{run_id}/trades.parquet"
            )
            splits = manifest["time_splits"]
            result = BaselineTradeReconciler().reconcile(
                bars=bars,
                trades=trades,
                fee_per_side=float(manifest["cost_model"]["fee_per_side"]),
                slippage_bps_per_side=float(
                    manifest["cost_model"]["slippage_bps_per_side"]
                ),
                train_end=splits["train"]["end_utc_exclusive"],
                validation_end=splits["validation"]["end_utc_exclusive"],
                sample_size=20,
            )
            payload = {
                "report_version": 1,
                "baseline_run_id": run_id,
                "strategy_version_id": manifest["strategy"]["strategy_version_id"],
                "market_profile": manifest["market_profile"],
                "validation_scope": manifest["validation_scope"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "locked_test_note": (
                    "This is code/execution reconciliation only. No strategy change or "
                    "parameter choice was made from the locked-test result."
                ),
                "external_platform_parity": "pending",
                **reconciliation_json(result),
            }
            json_key = f"reports/backtests/{run_id}-trade-reconciliation.json"
            markdown_key = f"reports/backtests/{run_id}-trade-reconciliation.md"
            if self.artifacts.exists(json_key) or self.artifacts.exists(markdown_key):
                raise FileExistsError("trade reconciliation artifacts already exist")
            json_bytes = (
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            markdown_bytes = self._markdown(payload).encode("utf-8")
            self.artifacts.put(json_key, json_bytes)
            self.artifacts.put(markdown_key, markdown_bytes)

            created_at = payload["created_at"]
            self.repository.create_report(
                Report(
                    id=new_id("report"),
                    job_id=manifest["job_id"],
                    report_type="trade_reconciliation",
                    artifact_key=markdown_key,
                    summary=dict(payload["summary"]),
                    created_at=created_at,
                )
            )
            self.service.record_tool_call(
                agent_run_id=agent_run.id,
                tool_name="generate_report",
                sanitized_input={
                    "baseline_run_id": run_id,
                    "sample_size": 20,
                    "baseline_change": False,
                },
                sanitized_output={
                    "all_checks_passed": payload["summary"]["all_checks_passed"],
                    "markdown_artifact_key": markdown_key,
                    "json_artifact_key": json_key,
                    "external_platform_parity": "pending",
                },
                status="completed",
            )
            self.service.record_artifact(
                agent_run_id=agent_run.id,
                artifact_type="report",
                artifact_key=markdown_key,
                checksum="sha256:" + hashlib.sha256(markdown_bytes).hexdigest(),
            )
            self.service.record_artifact(
                agent_run_id=agent_run.id,
                artifact_type="report",
                artifact_key=json_key,
                checksum="sha256:" + hashlib.sha256(json_bytes).hexdigest(),
            )
            self.repository.update_agent_run_status(agent_run.id, status="completed")
            self.repository.append_event(
                AuditEvent(
                    id=None,
                    event_type="baseline_trade_reconciliation.completed",
                    aggregate_type="strategy_version",
                    aggregate_id=manifest["strategy"]["strategy_version_id"],
                    actor_type="external_agent",
                    payload={
                        "baseline_run_id": run_id,
                        "all_checks_passed": payload["summary"]["all_checks_passed"],
                        "sample_size": payload["summary"]["sample_size"],
                        "baseline_changed": False,
                        "external_platform_parity": "pending",
                        "artifact_keys": [markdown_key, json_key],
                    },
                    created_at=created_at,
                )
            )
            return {
                "agent_run_id": agent_run.id,
                "summary": payload["summary"],
                "artifact_keys": [markdown_key, json_key],
            }
        except Exception:
            self.repository.update_agent_run_status(agent_run.id, status="failed")
            raise

    @staticmethod
    def _markdown(payload: dict[str, Any]) -> str:
        summary = payload["summary"]
        lines = [
            "# Baseline v0 逐笔交易对账报告",
            "",
            f"- Baseline Run：`{payload['baseline_run_id']}`",
            f"- 策略版本：`{payload['strategy_version_id']}`",
            f"- Market Profile：`{payload['market_profile']}`（仅 Binance）",
            "- 对账性质：内部规则与成交数学一致性；未修改 baseline，未进行参数优化。",
            "- 外部平台逐笔对齐：尚未完成。",
            "",
            "## 全量检查",
            "",
            f"- 检查交易：{summary['trades_checked']} 笔",
            f"- 入场信号不匹配：{summary['entry_signal_mismatches']}",
            f"- 重叠持仓：{summary['position_overlaps']}",
            f"- 非法退出成交：{summary['invalid_exit_fills']}",
            f"- 滑点计算不匹配：{summary['slippage_mismatches']}",
            f"- 手续费/权益计算不匹配：{summary['fee_math_mismatches']}",
            f"- 入场同根止损：{summary['same_bar_round_trips']} 笔",
            f"- 跳空按开盘成交：{summary['gap_stop_fills']} 笔",
            f"- 内部一致性结论：{'通过' if summary['all_checks_passed'] else '失败'}",
            "",
            "## 20 笔代表交易",
            "",
            "| ID | 类别 | 区间 | 方向 | 确认可用/入场 UTC | 退出 UTC | 原因 | 净收益 | 检查 |",
            "|---:|---|---|---|---|---|---|---:|---|",
        ]
        for item in payload["samples"]:
            checks = item["checks"]
            ok = all(
                checks[key]
                for key in (
                    "entry_signal_ok",
                    "exit_fill_ok",
                    "slippage_ok",
                    "fee_math_ok",
                )
            )
            lines.append(
                "| {trade_id} | {category} | {split} | {side} | {available} / {entry} | {exit} | {reason} | {ret:.3%} | {check} |".format(
                    trade_id=item["trade_id"],
                    category=item["selection_category"],
                    split=item["split"],
                    side=item["side"],
                    available=item["signal_event"]["confirmation_available_at"],
                    entry=item["entry_time"],
                    exit=item["exit_time"],
                    reason=item["exit_reason"],
                    ret=item["net_return"],
                    check="通过" if ok else "失败",
                )
            )
        lines.extend(
            [
                "",
                "## 观察（不是优化结论）",
                "",
                f"- {summary['same_bar_round_trips']} 笔交易在入场同一根 K 线触发保护止损，说明原始规则在 15m 上产生较多极短持仓。",
                f"- {summary['gap_stop_fills']} 笔止损按不利开盘价格成交，已遵守跳空保守处理。",
                "- 这些事实只能用于检查实现是否符合冻结规则，不能直接据此修改策略；若修改，必须创建新版本并处理锁定测试污染。",
                "",
                "## 尚未通过的外部门槛",
                "",
                "尚未获得 TradingView 或独立 Freqtrade 参考交易序列，因此目前只能确认项目内部实现自洽，不能宣称已与参考平台逐笔对齐。下一步需要选择同一数据窗口导出参考信号或解决 Freqtrade exchangeInfo 元数据问题。",
                "",
            ]
        )
        return "\n".join(lines)
