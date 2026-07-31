"use client";

import { useState } from "react";

import {
  BatchSummary,
  RunBundle,
  Trial,
} from "@/lib/api";
import { RegimeEvidence } from "@/components/studio/regime-evidence";
import {
  PerformanceCharts,
  TrialMetricComparison,
} from "@/components/studio/research-charts";
import {
  cnEvidence,
  formatNumber,
  formatPercent,
  formatSignedPercent,
  formatTrialParameters,
  trialConclusion,
  trialDelta,
} from "@/components/studio/studio-labels";
import {
  EmptyState,
  StatusBadge,
} from "@/components/studio/studio-primitives";

export type ResearchAnalysisView =
  | "performance"
  | "parameters"
  | "regimes";

export function ResearchAnalysis({
  bundles,
  trials,
  batchSummary,
  regimeMetrics,
  regimeEvidenceStatus,
  formalRegimeValidation,
  activeView,
  onViewChange,
  showNavigation = true,
}: {
  bundles: RunBundle[];
  trials: Trial[];
  batchSummary: BatchSummary | undefined;
  regimeMetrics: Record<string, Record<string, number>>;
  regimeEvidenceStatus: string;
  formalRegimeValidation: boolean;
  activeView?: ResearchAnalysisView;
  onViewChange?: (view: ResearchAnalysisView) => void;
  showNavigation?: boolean;
}) {
  const [internalView, setInternalView] =
    useState<ResearchAnalysisView>("performance");
  const currentView = activeView ?? internalView;
  const changeView = (view: ResearchAnalysisView) => {
    if (onViewChange) {
      onViewChange(view);
      return;
    }
    setInternalView(view);
  };
  const tabs: Array<{
    id: ResearchAnalysisView;
    label: string;
    description: string;
    count: string;
  }> = [
    {
      id: "performance",
      label: "走势与风险",
      description: "行情、资金与回撤",
      count: bundles.length ? `${bundles.length} 份结果` : "暂无曲线",
    },
    {
      id: "parameters",
      label: "参数试验",
      description: "方案表与敏感性",
      count: trials.length ? `${trials.length} 个方案` : "暂无方案",
    },
    {
      id: "regimes",
      label: "行情适配",
      description: "适合与不适合证据",
      count: Object.keys(regimeMetrics).length
        ? `${Object.keys(regimeMetrics).length} 类行情`
        : "暂无样本",
    },
  ];

  return (
    <div className="space-y-5">
      {showNavigation ? (
        <>
          <div className="text-xs leading-5 text-slate-500">
            先选择要回答的问题，再按“指标表格 → 图形证据”阅读。所有内容只取当前研究会话和当前策略版本，不会与其他 AI 会话自动混合。
          </div>
          <div
            className="grid gap-2 sm:grid-cols-3"
            role="tablist"
            aria-label="研究结果分析"
          >
            {tabs.map((tab) => {
              const active = currentView === tab.id;
              return (
                <button
                  key={tab.id}
                  id={`analysis-tab-${tab.id}`}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  aria-controls={`analysis-panel-${tab.id}`}
                  onClick={() => changeView(tab.id)}
                  className={
                    active
                      ? "min-h-20 cursor-pointer rounded-xl border border-sky-300/30 bg-sky-300/10 px-3 py-3 text-left shadow-[inset_0_0_0_1px_rgba(125,211,252,0.04)] transition"
                      : "min-h-20 cursor-pointer rounded-xl border border-white/10 bg-black/10 px-3 py-3 text-left transition hover:border-white/20 hover:bg-white/[0.03]"
                  }
                >
                  <span
                    className={
                      active
                        ? "block text-sm font-medium text-sky-100"
                        : "block text-sm font-medium text-slate-300"
                    }
                  >
                    {tab.label}
                  </span>
                  <span className="mt-1 block text-[11px] leading-4 text-slate-500">
                    {tab.description}
                  </span>
                  <span
                    className={
                      active
                        ? "mt-2 inline-flex rounded-full bg-sky-300/10 px-2 py-0.5 text-[10px] text-sky-200"
                        : "mt-2 inline-flex rounded-full bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-500"
                    }
                  >
                    {tab.count}
                  </span>
                </button>
              );
            })}
          </div>
        </>
      ) : null}

      <div
        id={`analysis-panel-${currentView}`}
        role="tabpanel"
        aria-labelledby={
          showNavigation ? `analysis-tab-${currentView}` : undefined
        }
        className={
          showNavigation
            ? "rounded-xl border border-white/[0.08] bg-black/[0.08] p-3 md:p-4"
            : ""
        }
      >
        {currentView === "performance" ? (
          <AnalysisSection
            title="走势与风险"
            description="表格给出每条真实曲线的收益、最大回撤和覆盖区间；图形把行情与资金曲线放在同一日期轴上。最大回撤已在表格中保留，不再重复设置独立回撤页。"
          >
            <PerformanceCharts
              bundles={bundles}
              trialCount={trials.length}
            />
          </AnalysisSection>
        ) : null}

        {currentView === "parameters" ? (
          <ParameterAnalysis
            summary={batchSummary}
            trials={trials}
          />
        ) : null}

        {currentView === "regimes" ? (
          <AnalysisSection
            title="行情适配"
            description="先看各行情状态的精确样本与指标，再用下方条形图比较净收益方向。样本不足仍明确标记为证据不足。"
          >
            <RegimeEvidence
              metrics={regimeMetrics}
              evidenceStatus={regimeEvidenceStatus}
              formalValidation={formalRegimeValidation}
            />
            <div className="mt-4 border-t border-white/[0.06] pt-4 text-xs leading-5 text-slate-500">
              Pine 来源先检查语义、重绘、多周期和少量代表交易；完整 TradingView 对账在快速初筛与可行性通过后进行。
            </div>
          </AnalysisSection>
        ) : null}
      </div>
    </div>
  );
}

function ParameterAnalysis({
  summary,
  trials,
}: {
  summary: BatchSummary | undefined;
  trials: Trial[];
}) {
  if (!summary?.trial_count) {
    return (
      <AnalysisSection
        title="参数试验"
        description="批量任务完成后，上方显示所有参数方案的精确指标，下方显示参数与收益、回撤、盈亏效率之间的关系。"
      >
        <EmptyState>
          尚无真实批量结果。测试数据必须明确标记，不能伪装成盈利候选。
        </EmptyState>
      </AnalysisSection>
    );
  }

  return (
    <AnalysisSection
      title="参数试验"
      description="先在表格中确认每个参数方案的设置、收益和风险，再用图形观察参数敏感性与稳定区间。"
    >
      <div className="mb-4 grid gap-2 sm:grid-cols-3">
        <SummaryCard
          label="证据类型"
          value={cnEvidence(summary.evidence_mode)}
        />
        <SummaryCard
          label="完成情况"
          value={`${summary.succeeded_count}/${summary.trial_count} 个成功`}
        />
        <SummaryCard
          label="稳定方案"
          value={`${summary.stable_count} 个`}
        />
      </div>
      <div className="overflow-x-auto rounded-xl border border-white/10">
        <table className="w-full min-w-[780px] border-collapse text-left text-[11px] tabular-nums">
          <thead className="bg-white/[0.04] text-slate-400">
            <tr>
              <th className="px-2.5 py-2.5 font-medium">参数方案</th>
              <th className="px-2.5 py-2.5 font-medium">验证收益</th>
              <th className="px-2.5 py-2.5 font-medium">相对基准</th>
              <th className="px-2.5 py-2.5 font-medium">盈亏效率</th>
              <th className="px-2.5 py-2.5 font-medium">单笔期望</th>
              <th className="px-2.5 py-2.5 font-medium">最大回撤</th>
              <th className="px-2.5 py-2.5 font-medium">交易数</th>
              <th className="px-2.5 py-2.5 font-medium">结论</th>
            </tr>
          </thead>
          <tbody>
            {trials.map((trial) => {
              const delta = trialDelta(trial, trials);
              return (
                <tr
                  key={trial.id}
                  className="border-t border-white/[0.06] text-slate-300"
                >
                  <td className="px-2.5 py-2.5 text-slate-100">
                    {formatTrialParameters(trial.parameters)}
                  </td>
                  <td className="px-2.5 py-2.5">
                    {formatPercent(trial.metrics.validation_net_return)}
                  </td>
                  <td
                    className={`px-2.5 py-2.5 ${
                      delta > 0
                        ? "text-emerald-200"
                        : delta < 0
                          ? "text-rose-200"
                          : "text-slate-400"
                    }`}
                  >
                    {formatSignedPercent(delta)}
                  </td>
                  <td className="px-2.5 py-2.5">
                    {formatNumber(
                      trial.metrics.validation_profit_factor,
                      3,
                    )}
                  </td>
                  <td className="px-2.5 py-2.5">
                    {formatPercent(trial.metrics.validation_expectancy)}
                  </td>
                  <td className="px-2.5 py-2.5">
                    {formatPercent(
                      trial.metrics.validation_max_drawdown_abs,
                    )}
                  </td>
                  <td className="px-2.5 py-2.5">
                    {formatNumber(
                      trial.metrics.validation_trade_count,
                      0,
                    )}
                  </td>
                  <td className="px-2.5 py-2.5">
                    {trialConclusion(trial, trials)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <details className="mt-3 rounded-lg bg-white/[0.03] p-3 text-xs leading-5 text-slate-400">
        <summary className="cursor-pointer text-slate-300">
          查看稳定参数区间
        </summary>
        <div className="mt-2 break-words">
          {Object.entries(summary.stable_parameter_ranges)
            .map(([key, value]) => `${key}：${JSON.stringify(value)}`)
            .join("；") || "尚未形成连续稳定区间"}
        </div>
      </details>

      {!summary.research_conclusion_allowed ? (
        <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/10 p-3 text-xs leading-5 text-amber-100">
          当前是测试或不可用证据，只验证批量执行连线；这些数值不能形成候选、收益或稳定性结论。
        </div>
      ) : null}

      <div className="mt-5 border-t border-white/[0.06] pt-5">
        <TrialMetricComparison trials={trials} />
      </div>
    </AnalysisSection>
  );
}

function AnalysisSection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
        <p className="mt-1 max-w-4xl text-xs leading-5 text-slate-500">
          {description}
        </p>
      </div>
      {children}
    </div>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/[0.07] bg-white/[0.025] p-3">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="mt-1 flex items-center gap-2 text-sm text-slate-200">
        {label === "证据类型" ? (
          <StatusBadge value={value} tone="warning" />
        ) : (
          value
        )}
      </div>
    </div>
  );
}
