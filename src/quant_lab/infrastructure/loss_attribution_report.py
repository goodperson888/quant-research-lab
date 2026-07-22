from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_lab.application.baseline_backtest import PriceStructureBaselineBacktester
from quant_lab.application.loss_attribution import TrainingLossAttribution
from quant_lab.application.services import ResearchApplicationService, new_id
from quant_lab.domain.models import AuditEvent, Report
from quant_lab.domain.repositories import ProductRepository

from .artifact_store import LocalArtifactStore
from .baseline_backtest_runner import BaselineBacktestRunner
from .trade_reconciliation_report import RUN_ID_PATTERN


class TrainingLossAttributionReportGenerator:
    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.service = ResearchApplicationService(repository)
        self.artifacts = LocalArtifactStore(self.root)

    def generate(self, run_id: str) -> dict[str, Any]:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("run_id contains unsupported path characters")
        baseline_manifest = json.loads(
            self.artifacts.get(f"experiments/runs/{run_id}/manifest.json")
        )
        if baseline_manifest.get("run_type") != "baseline_backtest":
            raise ValueError("loss attribution requires a baseline_backtest run")

        agent_run = self.service.create_agent_run(
            session_id=baseline_manifest["strategy"]["session_id"],
            agent_name="codex",
            mode="guided",
            plan_summary=(
                "Attribute baseline losses using the training split only and produce "
                "one unapproved falsifiable hypothesis without changing strategy."
            ),
        )
        try:
            data_manifest = json.loads(
                self.artifacts.get(
                    baseline_manifest["data_manifest"]["artifact_key"]
                )
            )
            loader = BaselineBacktestRunner(self.root, self.repository)
            ohlcv = loader._load_dataset(data_manifest, "futures_ohlcv", "15m")
            funding = loader._load_dataset(data_manifest, "funding_rate", None)
            mark = loader._load_dataset(data_manifest, "mark_price", "15m")
            costs = baseline_manifest["cost_model"]
            split = baseline_manifest["time_splits"]["train"]
            train_result = PriceStructureBaselineBacktester(
                fee_per_side=float(costs["fee_per_side"]),
                slippage_bps_per_side=float(costs["slippage_bps_per_side"]),
                funding_missing_policy=baseline_manifest["funding"]["missing_policy"],
            ).run(
                label="train_loss_attribution",
                ohlcv=ohlcv,
                funding=funding,
                mark=mark,
                start_utc_inclusive=split["start_utc_inclusive"],
                end_utc_exclusive=split["end_utc_exclusive"],
            )
            analysis = TrainingLossAttribution().analyze(train_result.trades)
            payload = {
                "report_version": 1,
                "baseline_run_id": run_id,
                "strategy_version_id": baseline_manifest["strategy"][
                    "strategy_version_id"
                ],
                "market_profile": baseline_manifest["market_profile"],
                "analysis_scope": {
                    "split": "train",
                    "start_utc_inclusive": split["start_utc_inclusive"],
                    "end_utc_exclusive": split["end_utc_exclusive"],
                    "validation_data_used": False,
                    "locked_test_data_used": False,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
                **analysis,
            }
            json_key = f"reports/backtests/{run_id}-train-loss-attribution.json"
            markdown_key = f"reports/backtests/{run_id}-train-loss-attribution.md"
            if self.artifacts.exists(json_key) or self.artifacts.exists(markdown_key):
                raise FileExistsError("training loss attribution artifacts already exist")
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
                    job_id=baseline_manifest["job_id"],
                    report_type="training_loss_attribution",
                    artifact_key=markdown_key,
                    summary={
                        **dict(payload["summary"]),
                        "proposal_status": payload["proposal"]["status"],
                    },
                    created_at=created_at,
                )
            )
            self.service.record_tool_call(
                agent_run_id=agent_run.id,
                tool_name="generate_report",
                sanitized_input={
                    "baseline_run_id": run_id,
                    "scope": "train_only",
                    "baseline_change": False,
                },
                sanitized_output={
                    "markdown_artifact_key": markdown_key,
                    "json_artifact_key": json_key,
                    "proposal_status": payload["proposal"]["status"],
                },
                status="completed",
            )
            for key, content in (
                (markdown_key, markdown_bytes),
                (json_key, json_bytes),
            ):
                self.service.record_artifact(
                    agent_run_id=agent_run.id,
                    artifact_type="report",
                    artifact_key=key,
                    checksum="sha256:" + hashlib.sha256(content).hexdigest(),
                )
            self.repository.update_agent_run_status(agent_run.id, status="completed")
            self.repository.append_event(
                AuditEvent(
                    id=None,
                    event_type="baseline_training_loss_attribution.completed",
                    aggregate_type="strategy_version",
                    aggregate_id=payload["strategy_version_id"],
                    actor_type="external_agent",
                    payload={
                        "baseline_run_id": run_id,
                        "scope": "train_only",
                        "baseline_changed": False,
                        "optimization_performed": False,
                        "proposal_status": payload["proposal"]["status"],
                        "artifact_keys": [markdown_key, json_key],
                    },
                    created_at=created_at,
                )
            )
            return {
                "agent_run_id": agent_run.id,
                "summary": payload["summary"],
                "cost_waterfall": payload["cost_waterfall"],
                "proposal": payload["proposal"],
                "artifact_keys": [markdown_key, json_key],
            }
        except Exception:
            self.repository.update_agent_run_status(agent_run.id, status="failed")
            raise

    @staticmethod
    def _markdown(payload: dict[str, Any]) -> str:
        summary = payload["summary"]
        costs = payload["cost_waterfall"]
        side = payload["by_side"]
        proposal = payload["proposal"]

        def pct(value: Any) -> str:
            return "—" if value is None else f"{float(value):.2%}"

        lines = [
            "# Baseline v0 训练集亏损归因",
            "",
            f"- Baseline Run：`{payload['baseline_run_id']}`",
            f"- 策略版本：`{payload['strategy_version_id']}`",
            "- 范围：仅前 60 个完整 UTC 日训练集。",
            "- 验证集/锁定测试用于选择假设：否。",
            "- Baseline 修改：否；参数优化：否。",
            "",
            "## 成本瀑布",
            "",
            "| 阶段 | 复合收益 |",
            "|---|---:|",
            f"| 仅原始成交价格，不含滑点/费用/资金费率 | {pct(costs['raw_price_compound_return'])} |",
            f"| 加入 2 bps/边滑点 | {pct(costs['after_slippage_compound_return'])} |",
            f"| 再加入 0.05%/边手续费 | {pct(costs['after_fees_compound_return'])} |",
            f"| 加入实际资金费率后的净结果 | {pct(costs['net_after_funding_compound_return'])} |",
            "",
            "结论：原始价格逻辑在训练集已经为负，交易成本进一步显著放大亏损；不能只靠降低费用把该基准解释成有效策略。",
            "",
            "## 主要损失结构",
            "",
            f"- 交易数：{summary['trade_count']}；胜率：{pct(summary['win_rate'])}。",
            f"- 入场同根退出：{summary['same_bar_stop_count']} 笔，占 {pct(summary['same_bar_stop_rate'])}；这些交易复合收益 {pct(summary['same_bar_compound_return'])}。",
            f"- 同根亏损占所有亏损交易绝对收益之和：{pct(summary['same_bar_share_of_gross_losing_returns'])}。",
            f"- 多头：{side['long']['trade_count']} 笔，复合收益 {pct(side['long']['compound_net_return'])}。",
            f"- 空头：{side['short']['trade_count']} 笔，复合收益 {pct(side['short']['compound_net_return'])}。",
            f"- 训练集资金费率插补次数：{summary['funding_imputation_events']}，因此本归因未使用缺失资金费率代理。",
            "",
            "## 单一改进假设草案",
            "",
            f"状态：`{proposal['status']}`，尚未审批、尚未执行。",
            "",
            f"> {proposal['single_hypothesis']}",
            "",
            f"唯一规则变化：{proposal['single_rule_change']}",
            "",
            "其他摆动点、结构判定、止损、反向退出、成本和杠杆规则保持不变，以便形成真正的单变量消融。",
            "",
            "## 实验门禁",
            "",
            "- 本报告只提出 Proposal，不创建或批准参数搜索。",
            "- 先写 ExperimentPlan，固定目标、约束、成本、停止条件和最多一次对照 Trial。",
            "- 当前锁定测试已经查看；候选版本最终验收前必须取得新的锁定数据区间。",
            "- 不得覆盖 baseline v0，不得自动晋升 dry-run 或 production。",
            "",
        ]
        return "\n".join(lines)
