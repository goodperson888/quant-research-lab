import { BatchSummary, Job } from "@/lib/api";
import { cnStatus } from "@/components/studio/studio-labels";
import {
  EmptyState,
  MetricRow,
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

export function BatchProgressPanel({
  summary,
  job,
  onCancel,
}: {
  summary: BatchSummary | undefined;
  job: Job | undefined;
  onCancel: (jobId: string) => void;
}) {
  if (!summary) {
    return (
      <EmptyState>
        批量调参只会在具体改进方案、不可变候选、实验计划和预算全部批准后开始。
      </EmptyState>
    );
  }
  const total = finiteOr(summary.total_trials, summary.trial_count);
  const completed = finiteOr(summary.completed_trials, summary.trial_count);
  const succeeded = finiteOr(summary.succeeded_trials, summary.succeeded_count);
  const failed = finiteOr(
    summary.failed_trials,
    Math.max(completed - succeeded, 0),
  );
  const remaining = finiteOr(
    summary.remaining_trials,
    Math.max(total - completed, 0),
  );
  const jobStatus = summary.job_status ?? job?.status ?? "completed";
  const percent = total ? Math.min((completed / total) * 100, 100) : 0;
  const running = ["queued", "running"].includes(jobStatus);
  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center justify-between gap-3">
        <StatusBadge
          value={cnStatus(jobStatus)}
          tone={running ? "warning" : failed ? "danger" : "positive"}
        />
        <span className="text-slate-400">
          {completed} / {total} 个参数方案
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className="h-full rounded-full bg-emerald-300/70 transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <ProgressNumber label="成功" value={succeeded} />
        <ProgressNumber label="失败" value={failed} />
        <ProgressNumber label="剩余" value={remaining} />
      </div>
      <MetricRow
        label="并发"
        value={
          Number.isFinite(summary.concurrency)
            ? `${summary.concurrency} 个`
            : "尚未记录"
        }
      />
      <MetricRow
        label="耗时"
        value={
          Number.isFinite(summary.elapsed_seconds)
            ? `${Math.round((summary.elapsed_seconds ?? 0) / 60)} 分钟`
            : "尚未记录"
        }
      />
      <MetricRow
        label="峰值内存"
        value={
          Number.isFinite(summary.peak_rss_mb)
            ? `${(summary.peak_rss_mb ?? 0).toFixed(0)} MB`
            : "尚未记录"
        }
      />
      <div className="rounded-lg bg-white/[0.03] p-2 leading-5 text-slate-400">
        {summary.stop_reason
          ? `停止原因：${summary.stop_reason}`
          : summary.continue_reason ??
            (running ? "系统正在已批准预算内继续。" : "当前批次范围已结束。")}
      </div>
      {running && job ? (
        <button
          type="button"
          onClick={() => onCancel(job.id)}
          className="w-full rounded-lg border border-rose-300/20 px-3 py-2 text-rose-200"
        >
          在安全边界停止批次
        </button>
      ) : null}
      <TechnicalDetails>
        {summary.job_id ? <TechnicalId label="后台任务" value={summary.job_id} /> : null}
        <TechnicalId label="实验计划" value={summary.experiment_plan_id} />
      </TechnicalDetails>
    </div>
  );
}

function finiteOr(
  value: number | null | undefined,
  fallback: number | null | undefined,
) {
  if (Number.isFinite(value)) return value ?? 0;
  return Number.isFinite(fallback) ? (fallback ?? 0) : 0;
}

function ProgressNumber({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-2 py-2">
      <div className="text-base font-semibold text-slate-100">{value}</div>
      <div className="mt-0.5 text-slate-500">{label}</div>
    </div>
  );
}
