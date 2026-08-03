"use client";

import { useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { apiFetch, Job } from "@/lib/api";
import {
  EmptyState,
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

type JobFilter = "all" | "active" | "completed" | "failed";

export function JobList() {
  const [filter, setFilter] = useState<JobFilter>("all");
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["jobs"],
    queryFn: () => apiFetch<Job[]>("/api/jobs"),
  });
  const retryJob = useMutation({
    mutationFn: (jobId: string) =>
      apiFetch<Job>(`/api/jobs/${jobId}/retry`, {
        method: "POST",
        body: JSON.stringify({
          subject_id: jobId,
          confirmed_by_user: true,
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });
  if (query.isPending) {
    return <div className="text-sm text-slate-500">正在读取后台任务……</div>;
  }
  if (query.isError) {
    return (
      <div className="rounded-xl border border-rose-300/20 bg-rose-300/[0.06] p-4 text-sm text-rose-100">
        无法读取后台任务：{query.error.message}
      </div>
    );
  }
  if (!query.data.length) {
    return (
      <EmptyState>
        目前没有后台任务。启动快速初筛、参数试验或诊断后，执行进度会显示在这里。
      </EmptyState>
    );
  }
  const jobs = [...query.data].sort(
    (left, right) =>
      Date.parse(right.created_at) - Date.parse(left.created_at),
  );
  const counts = {
    active: jobs.filter((job) => ["queued", "running"].includes(job.status))
      .length,
    completed: jobs.filter((job) => job.status === "succeeded").length,
    failed: jobs.filter((job) =>
      ["failed", "cancelled"].includes(job.status),
    ).length,
  };
  const filtered = jobs.filter((job) => matchesFilter(job, filter));
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <Metric label="运行中 / 排队" value={counts.active} tone="warning" />
        <Metric label="已完成" value={counts.completed} tone="positive" />
        <Metric label="失败 / 取消" value={counts.failed} tone="danger" />
      </div>
      <div
        className="flex max-w-full gap-2 overflow-x-auto pb-1"
        role="tablist"
        aria-label="后台任务筛选"
      >
        {[
          ["all", "全部", jobs.length],
          ["active", "运行中", counts.active],
          ["completed", "已完成", counts.completed],
          ["failed", "失败或取消", counts.failed],
        ].map(([id, label, count]) => (
          <button
            key={String(id)}
            type="button"
            role="tab"
            aria-selected={filter === id}
            onClick={() => setFilter(id as JobFilter)}
            className={
              filter === id
                ? "min-h-11 shrink-0 rounded-xl border border-sky-300/30 bg-sky-300/10 px-3 text-xs text-sky-100"
                : "min-h-11 shrink-0 rounded-xl border border-white/10 px-3 text-xs text-slate-400 hover:border-white/20 hover:bg-white/[0.03]"
            }
          >
            {label} {count}
          </button>
        ))}
      </div>
      <div className="space-y-3">
        {filtered.map((job) => (
          <article
            key={job.id}
            className="rounded-xl border border-white/10 bg-white/[0.025] p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-medium text-slate-100">
                  {jobTypeLabel(job.job_type)}
                </h2>
                <div className="mt-1 text-xs text-slate-500">
                  创建于 {formatDate(job.created_at)} · 更新于{" "}
                  {formatDate(job.updated_at)}
                </div>
              </div>
              <StatusBadge
                value={statusLabel(job.status)}
                tone={statusTone(job.status)}
              />
            </div>
            {job.error ? (
              <div className="mt-3 rounded-lg border border-rose-300/15 bg-rose-300/[0.05] px-3 py-3 text-xs leading-5 text-rose-100/80">
                <div>停止原因：{jobErrorLabel(job.error)}</div>
                <div className="mt-2 text-slate-400">
                  下一步：{jobNextAction(job)}
                </div>
                {canRetry(job) ? (
                  <button
                    type="button"
                    onClick={() => retryJob.mutate(job.id)}
                    disabled={retryJob.isPending}
                    className="mt-3 min-h-11 rounded-xl border border-rose-200/20 bg-rose-200/[0.08] px-4 text-sm font-medium text-rose-50 transition hover:bg-rose-200/[0.12] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {retryJob.isPending
                      ? "正在创建重试任务…"
                      : "确认修复后重试原范围"}
                  </button>
                ) : null}
              </div>
            ) : (
              <div className="mt-3 text-xs leading-5 text-slate-500">
                {job.status === "succeeded"
                  ? "任务已完成，相关证据可在“研究报告”或当前研究会话中查看。"
                  : job.status === "running"
                    ? "任务正在执行；刷新页面可读取最新状态。"
                    : job.status === "queued"
                      ? "任务已进入队列，等待本地 Worker 执行。"
                      : "任务没有登记额外错误信息。"}
              </div>
            )}
            {retryJob.isError && retryJob.variables === job.id ? (
              <div className="mt-2 text-xs leading-5 text-amber-200">
                无法重试：{retryErrorLabel(retryJob.error.message)}
              </div>
            ) : null}
            <TechnicalDetails label="查看任务技术信息">
              <TechnicalId label="任务对象" value={job.id} />
              <TechnicalId
                label="任务类型"
                value={job.job_type}
              />
            </TechnicalDetails>
          </article>
        ))}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "positive" | "warning" | "danger";
}) {
  const color = {
    positive: "text-emerald-200",
    warning: "text-amber-200",
    danger: "text-rose-200",
  }[tone];
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-2 text-2xl font-semibold ${color}`}>{value}</div>
    </div>
  );
}

function matchesFilter(job: Job, filter: JobFilter) {
  if (filter === "all") return true;
  if (filter === "active") return ["queued", "running"].includes(job.status);
  if (filter === "completed") return job.status === "succeeded";
  return ["failed", "cancelled"].includes(job.status);
}

function jobTypeLabel(value: string) {
  return {
    baseline_backtest: "基准回测",
    fast_screen: "快速初筛",
    parameter_search: "批量参数试验",
    research_diagnostic: "亏损与组件诊断",
    research_diagnostics: "亏损与组件诊断",
    regime_validation: "行情适配验证",
    correctness_diagnostics: "正确性诊断",
    engine_reconciliation: "第二引擎对账",
    stress_test: "压力测试",
    pipeline_execution: "策略快速研究流程",
    backtest: "策略回测",
  }[value] ?? value;
}

function jobErrorLabel(value: string) {
  if (
    value.includes(
      "reviewed StrategySpec has no authorized pipeline metadata",
    )
  ) {
    return "旧版执行程序没有找到当前策略的受控自动研究配置。新版通用策略执行器已补充；重启本地服务后可显式重试原批准范围。";
  }
  if (value.includes("incompatible merge keys")) {
    return "行情或交易时间字段精度不一致，诊断数据无法合并。需要更新到已修复版本后再重试。";
  }
  if (value.includes("invalid UTC split interval")) {
    return "研究数据切分区间无效或超出当前数据范围。请重新生成不重叠的训练、验证和保留区间。";
  }
  const prerequisite = value.match(
    /^authorization stage prerequisites are incomplete: (.+)$/,
  );
  if (prerequisite) {
    return `一次性研究授权的前置步骤尚未完成：${stageLabel(prerequisite[1])}。`;
  }
  return value;
}

function jobNextAction(job: Job) {
  if (
    job.error?.includes(
      "reviewed StrategySpec has no authorized pipeline metadata",
    )
  ) {
    return "先确认本地服务已重启到最新代码，再点击下方按钮；不会重新批准更大范围，也不会使用最终保留测试。";
  }
  if (job.job_type === "parameter_search") {
    return "修复资源或数据问题后可恢复未完成参数方案，已经完成的结果不会重跑。";
  }
  if (job.job_type === "pipeline_execution") {
    return "如果本任务尚未生成任何阶段证据，可重试原授权；若已有阶段证据，需要回到工作台新建授权。";
  }
  return "保留失败证据，回到研究工作台决定修复依赖或停止当前分支。";
}

function canRetry(job: Job) {
  return (
    ["failed", "cancelled"].includes(job.status) &&
    ["parameter_search", "pipeline_execution"].includes(job.job_type)
  );
}

function retryErrorLabel(value: string) {
  if (
    value.includes(
      "pipeline retry after recorded stage evidence requires a new authorization",
    )
  ) {
    return "这次任务已经保存了阶段证据，不能原地重跑。请回到研究工作台重新批准一次明确范围。";
  }
  if (
    value.includes(
      "pipeline authorization is no longer active",
    )
  ) {
    return "原研究授权已失效，请回到研究工作台重新批准快速初筛。";
  }
  return value;
}

function stageLabel(value: string) {
  return (
    {
      loss_attribution: "亏损原因分析",
      regime_diagnostic: "行情适配初查",
      component_hypothesis_generation: "局部改进方向",
    }[value] ?? value
  );
}

function statusLabel(value: string) {
  return {
    queued: "排队中",
    running: "运行中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
  }[value] ?? value;
}

function statusTone(value: string) {
  if (value === "succeeded") return "positive" as const;
  if (["failed", "cancelled"].includes(value)) return "danger" as const;
  return "warning" as const;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}
