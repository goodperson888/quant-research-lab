"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch, RunBundle } from "@/lib/api";
import {
  EmptyState,
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

export function ReportList() {
  const query = useQuery({
    queryKey: ["run-bundles"],
    queryFn: () => apiFetch<RunBundle[]>("/api/run-bundles"),
  });
  if (query.isPending) {
    return <div className="text-sm text-slate-500">正在读取研究报告……</div>;
  }
  if (query.isError) {
    return (
      <div className="rounded-xl border border-rose-300/20 bg-rose-300/[0.06] p-4 text-sm text-rose-100">
        无法读取研究报告：{query.error.message}
      </div>
    );
  }
  const reports = [...query.data].sort(
    (left, right) =>
      Date.parse(right.created_at) - Date.parse(left.created_at),
  );
  if (!reports.length) {
    return (
      <EmptyState>
        当前还没有真实研究报告。完成快速初筛、参数试验或诊断后，报告会自动进入这里。
      </EmptyState>
    );
  }
  const succeeded = reports.filter((item) => item.status === "succeeded").length;
  const failed = reports.filter((item) => item.status === "failed").length;
  const withCurves = reports.filter((item) =>
    ["baseline_backtest", "fast_screen", "smoke"].some((token) =>
      item.report_type.includes(token),
    ),
  ).length;
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <SummaryCard label="报告总数" value={reports.length} />
        <SummaryCard label="执行成功" value={succeeded} tone="positive" />
        <SummaryCard
          label="失败 / 有曲线结果"
          value={`${failed} / ${withCurves}`}
        />
      </div>
      <div className="rounded-xl border border-sky-300/15 bg-sky-300/[0.05] px-4 py-3 text-xs leading-5 text-sky-100/80">
        报告是一次运行的证据包，不等于“有效策略”。是否值得继续研究仍以可行性门槛、
        样本外、成本和稳定性结论为准。
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        {reports.slice(0, 40).map((report) => (
          <article
            key={report.bundle_id}
            className="rounded-xl border border-white/10 bg-white/[0.025] p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-medium text-slate-100">
                  {reportTypeLabel(report.report_type)}
                </h2>
                <div className="mt-1 text-xs text-slate-500">
                  {jobTypeLabel(report.job_type)} · {formatDate(report.created_at)}
                </div>
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
            <div className="mt-3 flex flex-wrap gap-2">
              {summaryFacts(report.summary).map((fact) => (
                <span
                  key={`${fact.label}:${fact.value}`}
                  className="rounded-full border border-white/[0.08] bg-black/10 px-2.5 py-1 text-[11px] text-slate-400"
                >
                  {fact.label}：{fact.value}
                </span>
              ))}
            </div>
            <TechnicalDetails label="查看报告技术信息">
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
          </article>
        ))}
      </div>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  tone?: "positive" | "neutral";
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div
        className={
          tone === "positive"
            ? "mt-2 text-2xl font-semibold text-emerald-200"
            : "mt-2 text-2xl font-semibold text-slate-100"
        }
      >
        {value}
      </div>
    </div>
  );
}

function summaryFacts(summary: Record<string, unknown>) {
  const candidates: Array<[string, string]> = [
    ["结论", textValue(summary.recommended_decision)],
    ["状态", textValue(summary.status)],
    ["方案数", numberValue(summary.trial_count)],
    ["稳定方案", numberValue(summary.stable_count)],
    ["可行性", textValue(summary.gate_result)],
  ];
  return candidates
    .filter((item): item is [string, string] => Boolean(item[1]))
    .slice(0, 3)
    .map(([label, value]) => ({ label, value }));
}

function textValue(value: unknown) {
  if (typeof value !== "string" || !value.trim()) return "";
  return {
    reject_proposal: "拒绝方案",
    reject_proposal_preserve_diagnostic_evidence: "拒绝方案并保留诊断证据",
    accept_proposal: "接受方案",
    hypothesis_failed: "假设未成立",
    pass: "通过",
    fail: "未通过",
  }[value] ?? value;
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-CN")
    : "";
}

function reportTypeLabel(value: string) {
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
  return {
    baseline_backtest: "基准回测",
    fast_screen: "快速初筛",
    parameter_search: "参数试验",
    research_diagnostic: "研究诊断",
    research_diagnostics: "研究诊断",
    regime_validation: "行情验证",
    stress_test: "压力测试",
  }[value] ?? value;
}

function statusLabel(value: string) {
  return {
    succeeded: "已完成",
    failed: "失败",
    running: "运行中",
    queued: "排队中",
    cancelled: "已取消",
  }[value] ?? value;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}
