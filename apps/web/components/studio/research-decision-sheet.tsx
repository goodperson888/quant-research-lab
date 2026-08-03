import {
  BatchSummary,
  CandidateValidationSummary,
  EngineReconciliationStatus,
} from "@/lib/api";
import { StrategyConclusion } from "@/components/studio/studio-overview";
import { StatusBadge } from "@/components/studio/studio-primitives";

export function ResearchDecisionSheet({
  conclusion,
  viabilityStatus,
  viabilityReasons,
  regimeGroups,
  batchSummary,
  candidateValidation,
  componentCandidateCount,
  reportCount,
  reconciliation,
}: {
  conclusion: StrategyConclusion;
  viabilityStatus: string | undefined;
  viabilityReasons: string[];
  regimeGroups: Record<string, string[]>;
  batchSummary: BatchSummary | undefined;
  candidateValidation: CandidateValidationSummary | undefined;
  componentCandidateCount: number;
  reportCount: number;
  reconciliation: EngineReconciliationStatus | undefined;
}) {
  const recommendation = batchSummary?.recommendation;
  const metrics =
    candidateValidation?.locked_test_metrics ??
    recommendation?.metrics ??
    batchSummary?.representative_stable_metrics ??
    {};
  const completedBatchWithoutStableCandidate =
    Boolean(batchSummary?.trial_count) &&
    (batchSummary?.stable_count ?? 0) === 0;
  const nextAction =
    viabilityStatus === "failed"
      ? "停止当前完整策略调参；回到亏损归因，只保留有独立证据的局部组件，或建立一个新的可解释假设。"
      : completedBatchWithoutStableCandidate
        ? "当前批次没有稳定参数方案。不要扩大搜索范围；先回到亏损归因，再提出一个可解释的单一改进假设。"
        : candidateValidation?.next_action ??
          (viabilityStatus === "passed"
            ? "完成候选稳健性、第二引擎对账和最终保留测试后，再决定是否进入模拟运行。"
            : "先完成快速初筛和可行性门槛。");
  const tone =
    conclusion.tone === "success"
      ? "positive"
      : conclusion.tone === "danger"
        ? "danger"
        : "neutral";

  return (
    <section
      data-testid="research-decision-sheet"
      className="rounded-2xl border border-white/10 bg-[linear-gradient(145deg,rgba(16,185,129,.08),rgba(7,15,22,.96)_48%)] p-4 md:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-3xl">
          <div className="text-[11px] font-semibold tracking-[0.14em] text-emerald-300">
            一页研究结论
          </div>
          <h3 className="mt-2 text-xl font-semibold text-white">
            {conclusion.title}
          </h3>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            {conclusion.detail}
          </p>
        </div>
        <StatusBadge value={verdictLabel(conclusion)} tone={tone} />
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <DecisionMetric
          label="可行性门槛"
          value={stageStatus(viabilityStatus)}
        />
        <DecisionMetric
          label="稳定参数方案"
          value={
            batchSummary
              ? `${batchSummary.stable_count} / ${batchSummary.trial_count}`
              : "尚未运行"
          }
        />
        <DecisionMetric
          label="最终保留测试"
          value={lockedTestLabel(candidateValidation)}
        />
        <DecisionMetric
          label="第二引擎对账"
          value={
            reconciliation?.implementation_status === "not_connected"
              ? "尚未接入"
              : reconciliation?.eligible
                ? "等待执行"
                : "门槛未通过"
          }
        />
      </div>

      {Object.keys(metrics).length ? (
        <div className="mt-5">
          <div className="mb-2 text-xs font-medium text-slate-300">
            当前最有代表性的证据
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
            <EvidenceMetric
              label="净收益"
              value={metricPercent(
                metrics.total_return ??
                  metrics.validation_net_return,
              )}
            />
            <EvidenceMetric
              label="最大回撤"
              value={metricMagnitudePercent(
                Math.abs(
                  metrics.max_drawdown ??
                    metrics.validation_max_drawdown_abs ??
                    0,
                ),
              )}
            />
            <EvidenceMetric
              label="盈亏效率"
              value={metricNumber(
                metrics.profit_factor ??
                  metrics.validation_profit_factor,
              )}
            />
            <EvidenceMetric
              label="单笔期望"
              value={metricNumber(
                metrics.expectancy ??
                  metrics.validation_expectancy,
                5,
              )}
            />
            <EvidenceMetric
              label="交易次数"
              value={metricInteger(
                metrics.trade_count ??
                  metrics.validation_trade_count,
              )}
            />
          </div>
        </div>
      ) : null}

      <div className="mt-5 grid gap-3 lg:grid-cols-2">
        <EvidenceList
          title="最弱证据 / 为什么停"
          items={
            candidateValidation?.weakest_evidence.length
              ? candidateValidation.weakest_evidence
              : viabilityReasons.length
                ? viabilityReasons
                : ["尚未形成足够证据。"]
          }
        />
        <div className="rounded-xl border border-white/[0.08] bg-black/10 p-3">
          <div className="text-xs font-medium text-slate-300">
            行情适配
          </div>
          <div className="mt-2 space-y-1 text-xs leading-5 text-slate-400">
            <div>
              适合：
              {regimeList(regimeGroups.suitable ?? [], "尚未发现")}
            </div>
            <div>
              条件适合：
              {regimeList(
                regimeGroups.conditional ?? [],
                "尚未发现",
              )}
            </div>
            <div>
              不适合：
              {regimeList(regimeGroups.blocked ?? [], "尚无结论")}
            </div>
            {(regimeGroups.unknown ?? []).length ? (
              <div>
                证据不足：{(regimeGroups.unknown ?? []).join("、")}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="mt-5 rounded-xl border border-emerald-300/15 bg-emerald-300/[0.06] p-3">
        <div className="text-xs font-medium text-emerald-100">
          建议下一步
        </div>
        <p className="mt-1 text-sm leading-6 text-slate-300">
          {nextAction}
        </p>
      </div>

      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
        <span>已保存报告：{reportCount} 份</span>
        <span>可继续验证的组件：{componentCandidateCount} 个</span>
        <span>任何通过结论都不会自动进入实盘</span>
      </div>
    </section>
  );
}

function DecisionMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-black/10 p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-medium text-slate-100">{value}</div>
    </div>
  );
}

function EvidenceMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-medium tabular-nums text-slate-100">
        {value}
      </div>
    </div>
  );
}

function EvidenceList({
  title,
  items,
}: {
  title: string;
  items: string[];
}) {
  return (
    <div className="rounded-xl border border-amber-300/15 bg-amber-300/[0.05] p-3">
      <div className="text-xs font-medium text-amber-100">{title}</div>
      <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-300">
        {items.slice(0, 4).map((item) => (
          <li key={item}>· {item}</li>
        ))}
      </ul>
    </div>
  );
}

function verdictLabel(conclusion: StrategyConclusion) {
  if (conclusion.tone === "success") return "可继续审阅";
  if (conclusion.tone === "danger") return "停止或重建假设";
  return "等待证据";
}

function stageStatus(value: string | undefined) {
  return (
    {
      passed: "已通过",
      failed: "未通过",
      blocked: "被阻止",
      not_evaluated: "尚未评估",
    }[value ?? "not_evaluated"] ?? value ?? "尚未评估"
  );
}

function lockedTestLabel(
  summary: CandidateValidationSummary | undefined,
) {
  if (summary?.locked_test_decision === "passed") return "已通过";
  if (summary?.locked_test_decision === "failed") return "未通过";
  if (summary?.locked_test_status === "running") return "运行中";
  if (summary?.locked_test_status === "queued") return "排队中";
  if (summary?.decision_status === "ready_for_locked_test_review") {
    return "等待人工批准";
  }
  return "尚未进入";
}

function regimeList(values: string[], fallback: string) {
  return values.length ? values.join("、") : fallback;
}

function metricPercent(value: number | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
}

function metricMagnitudePercent(value: number | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${(Math.abs(value) * 100).toFixed(2)}%`;
}

function metricNumber(value: number | undefined, digits = 3) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

function metricInteger(value: number | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString("zh-CN");
}
