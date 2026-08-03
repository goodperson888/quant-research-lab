"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { PageFrame } from "@/components/page-frame";
import { PerformanceCharts } from "@/components/studio/research-charts";
import {
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";
import { apiFetch, RunBundle } from "@/lib/api";

export function ReportDetail({ bundleId }: { bundleId: string }) {
  const query = useQuery({
    queryKey: ["run-bundle", bundleId],
    queryFn: () =>
      apiFetch<RunBundle>(
        `/api/run-bundles/${encodeURIComponent(bundleId)}`,
      ),
  });

  if (query.isPending) {
    return (
      <PageFrame
        eyebrow="研究证据"
        title="正在读取报告"
        description="正在汇总关键指标、结论和可用图表。"
      >
        <div className="text-sm text-slate-500">请稍候……</div>
      </PageFrame>
    );
  }
  if (query.isError) {
    return (
      <PageFrame
        eyebrow="研究证据"
        title="报告读取失败"
        description="原始研究证据不会因此删除。"
      >
        <div className="rounded-xl border border-rose-300/20 bg-rose-300/[0.06] p-4 text-sm text-rose-100">
          {query.error.message}
        </div>
      </PageFrame>
    );
  }

  const report = query.data;
  const facts = reportFacts(report.summary);
  const decision = reportDecision(report);
  return (
    <PageFrame
      eyebrow="单次研究报告"
      title={reportTypeLabel(report.report_type)}
      description="先看结论和关键指标，再查看真实资金曲线及技术记录。单次报告不等同于已验证策略。"
    >
      <div className="mb-4 flex flex-wrap gap-2">
        <Link
          href="/reports"
          className="inline-flex min-h-11 items-center rounded-xl border border-white/10 px-4 text-sm text-slate-300 transition hover:border-white/20 hover:bg-white/[0.03]"
        >
          返回报告列表
        </Link>
        <Link
          href="/studio?stage=conclusion"
          className="inline-flex min-h-11 items-center rounded-xl border border-emerald-300/20 bg-emerald-300/[0.06] px-4 text-sm text-emerald-100 transition hover:bg-emerald-300/[0.1]"
        >
          回到研究结论
        </Link>
      </div>

      <section className="rounded-2xl border border-white/10 bg-white/[0.025] p-4 md:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs text-slate-500">
              {jobTypeLabel(report.job_type)} ·{" "}
              {formatDate(report.created_at)}
            </div>
            <h2 className="mt-2 text-xl font-semibold text-white">
              {decision.headline}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
              {decision.detail}
            </p>
          </div>
          <StatusBadge
            value={statusLabel(report.status)}
            tone={
              report.status === "succeeded"
                ? "positive"
                : report.status === "failed"
                  ? "danger"
                  : "warning"
            }
          />
        </div>

        {facts.length ? (
          <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {facts.map((fact) => (
              <div
                key={fact.label}
                className="rounded-xl border border-white/[0.08] bg-black/10 p-3"
              >
                <div className="text-xs text-slate-500">
                  {fact.label}
                </div>
                <div className="mt-1 text-sm font-medium tabular-nums text-slate-100">
                  {fact.value}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-5 rounded-xl border border-dashed border-white/15 p-4 text-sm text-slate-500">
            这份旧报告没有保存统一指标摘要；下方仍会尝试读取真实资金曲线和交易证据。
          </div>
        )}
      </section>

      <section className="mt-5 rounded-2xl border border-white/10 bg-white/[0.02] p-4 md:p-5">
        <h2 className="text-lg font-semibold text-slate-100">
          真实曲线与交易证据
        </h2>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          有资金曲线时可缩放、拖动并查看覆盖区间；没有保存曲线时会明确说明，不会生成假图。
        </p>
        <div className="mt-4">
          <PerformanceCharts
            bundles={[report]}
            trialCount={numericValue(report.summary.trial_count)}
          />
        </div>
      </section>

      <TechnicalDetails label="查看报告技术记录">
        <TechnicalId label="报告对象" value={report.bundle_id} />
        <TechnicalId label="后台任务" value={report.job_id} />
        {report.subject_id ? (
          <TechnicalId label="研究对象" value={report.subject_id} />
        ) : null}
        <TechnicalId
          label="报告文件"
          value={report.report_artifact_key}
        />
      </TechnicalDetails>
    </PageFrame>
  );
}

function reportFacts(summary: Record<string, unknown>) {
  const metrics = extractMetrics(summary);
  return [
    {
      label: "净收益",
      value: percentValue(
        metrics.total_return ?? metrics.validation_net_return,
      ),
    },
    {
      label: "最大回撤",
      value: magnitudePercentValue(
        Math.abs(
          numberOrUndefined(metrics.max_drawdown) ??
            numberOrUndefined(
              metrics.validation_max_drawdown_abs,
            ) ??
            0,
        ),
      ),
    },
    {
      label: "盈亏效率",
      value: decimalValue(
        metrics.profit_factor ??
          metrics.validation_profit_factor,
      ),
    },
    {
      label: "单笔期望",
      value: decimalValue(
        metrics.expectancy ?? metrics.validation_expectancy,
        5,
      ),
    },
    {
      label: "交易次数",
      value: integerValue(
        metrics.trade_count ?? metrics.validation_trade_count,
      ),
    },
  ].filter((item) => item.value !== "—");
}

function extractMetrics(
  summary: Record<string, unknown>,
): Record<string, number> {
  const direct = summary.metrics;
  if (isNumericRecord(direct)) return direct;
  if (
    direct &&
    typeof direct === "object" &&
    !Array.isArray(direct)
  ) {
    const mapping = direct as Record<string, unknown>;
    for (const key of ["locked_test", "validation", "train", "smoke"]) {
      if (isNumericRecord(mapping[key])) return mapping[key];
    }
  }
  const decision = summary.decision;
  if (
    decision &&
    typeof decision === "object" &&
    !Array.isArray(decision)
  ) {
    const nested = (decision as Record<string, unknown>).metrics;
    if (isNumericRecord(nested)) return nested;
  }
  return {};
}

function reportDecision(report: RunBundle) {
  const summary = report.summary;
  const decision =
    summary.decision &&
    typeof summary.decision === "object" &&
    !Array.isArray(summary.decision)
      ? (summary.decision as Record<string, unknown>)
      : {};
  const headline =
    textValue(decision.headline) ||
    decisionLabel(
      textValue(decision.status) ||
        textValue(summary.recommended_decision) ||
        textValue(summary.status),
    );
  return {
    headline: headline || "本次运行已保存研究证据",
    detail:
      textValue(decision.next_action) ||
      "请结合样本外、成本、稳定性和行情适配证据判断是否继续；本报告不会自动晋升策略。",
  };
}

function isNumericRecord(
  value: unknown,
): value is Record<string, number> {
  return Boolean(
    value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      Object.values(value).some(
        (item) =>
          typeof item === "number" && Number.isFinite(item),
      ),
  );
}

function numberOrUndefined(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function numericValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : 0;
}

function textValue(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function percentValue(value: unknown) {
  const numeric = numberOrUndefined(value);
  if (numeric === undefined) return "—";
  return `${numeric >= 0 ? "+" : ""}${(numeric * 100).toFixed(2)}%`;
}

function magnitudePercentValue(value: unknown) {
  const numeric = numberOrUndefined(value);
  return numeric === undefined
    ? "—"
    : `${(Math.abs(numeric) * 100).toFixed(2)}%`;
}

function decimalValue(value: unknown, digits = 3) {
  const numeric = numberOrUndefined(value);
  return numeric === undefined ? "—" : numeric.toFixed(digits);
}

function integerValue(value: unknown) {
  const numeric = numberOrUndefined(value);
  return numeric === undefined
    ? "—"
    : Math.round(numeric).toLocaleString("zh-CN");
}

function decisionLabel(value: string) {
  return (
    {
      passed: "本次证据通过",
      failed: "本次证据未通过",
      needs_revision: "候选证据仍不稳健",
      ready_for_locked_test_review:
        "证据允许审阅最终保留测试",
      reject_proposal: "拒绝当前方案",
      reject_proposal_preserve_diagnostic_evidence:
        "拒绝方案并保留诊断证据",
      hypothesis_failed: "当前假设未成立",
    }[value] ?? value
  );
}

function reportTypeLabel(value: string) {
  if (value.includes("locked")) return "最终保留测试报告";
  if (value.includes("candidate")) return "候选稳健性报告";
  if (value.includes("baseline")) return "冻结基准回测";
  if (value.includes("fast_screen")) return "快速初筛报告";
  if (value.includes("smoke")) return "小范围试跑报告";
  if (value.includes("parameter")) return "参数试验报告";
  if (value.includes("diagnostic")) return "研究诊断报告";
  if (value.includes("regime")) return "行情适配报告";
  if (value.includes("stress")) return "压力测试报告";
  return "研究结果报告";
}

function jobTypeLabel(value: string) {
  return (
    {
      baseline_backtest: "基准回测",
      backtest: "策略回测",
      fast_screen: "快速初筛",
      parameter_search: "参数试验",
      research_diagnostic: "研究诊断",
      research_diagnostics: "研究诊断",
      regime_validation: "行情验证",
      stress_test: "稳健性与保留测试",
    }[value] ?? value
  );
}

function statusLabel(value: string) {
  return (
    {
      succeeded: "已完成",
      failed: "失败",
      running: "运行中",
      queued: "排队中",
      cancelled: "已取消",
    }[value] ?? value
  );
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}
