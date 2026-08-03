import { CandidateValidationSummary } from "@/lib/api";
import {
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

export function CandidateValidationResultCard({
  summary,
  onLaunchLockedTest,
  lockedTestPending = false,
}: {
  summary: CandidateValidationSummary;
  onLaunchLockedTest?: () => void;
  lockedTestPending?: boolean;
}) {
  const running = ["queued", "running"].includes(summary.job_status);
  const lockedRunning = ["queued", "running"].includes(
    summary.locked_test_status ?? "not_started",
  );
  const lockedPassed = summary.locked_test_decision === "passed";
  const lockedFailed = summary.locked_test_decision === "failed";
  const ready =
    summary.decision_status === "ready_for_locked_test_review";
  const title = lockedPassed
    ? "最终保留测试通过，等待人工审阅"
    : lockedFailed
      ? "最终保留测试未通过"
      : lockedRunning
        ? "最终保留测试正在运行"
        : running
    ? "候选验证正在运行"
    : ready
      ? "证据允许审阅最终保留测试"
      : summary.decision_status === "needs_revision"
        ? "候选证据仍不稳健"
        : summary.job_status === "failed"
          ? "候选验证执行失败"
          : "候选验证尚未形成结论";
  const tone =
    lockedPassed || ready
      ? "positive"
      : running || lockedRunning
        ? "warning"
        : "danger";

  return (
    <section
      data-testid="candidate-validation-result"
      className="rounded-2xl border border-white/10 bg-[linear-gradient(145deg,rgba(56,189,248,.08),rgba(8,17,25,.94)_58%)] p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-semibold tracking-[0.14em] text-sky-300">
            最终研究判断
          </div>
          <h3 className="mt-2 text-lg font-semibold text-slate-100">{title}</h3>
        </div>
        <StatusBadge
          value={
            running
              ? summary.job_status === "queued"
                ? "等待后台执行"
                : "正在验证"
              : lockedRunning
                ? summary.locked_test_status === "queued"
                  ? "等待最终测试"
                  : "最终测试运行中"
                : lockedPassed
                  ? "最终测试通过"
                  : lockedFailed
                    ? "最终测试未通过"
              : ready
                ? "可审阅下一门禁"
                : "需要修改或停止"
          }
          tone={tone}
        />
      </div>

      {summary.decision_status ? (
        <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <EvidenceItem
            label="成本提高"
            value={summary.cost_sensitivity_passed ? "通过" : "未通过"}
            passed={Boolean(summary.cost_sensitivity_passed)}
          />
          <EvidenceItem
            label="滚动窗口"
            value={`${summary.positive_rolling_windows ?? 0} / ${summary.rolling_window_count ?? 0} 个非负`}
            passed={(summary.positive_rolling_windows ?? 0) >= 2}
          />
          <EvidenceItem
            label="参数扰动"
            value={`${Math.round((summary.perturbation_pass_ratio ?? 0) * 100)}% 通过`}
            passed={(summary.perturbation_pass_ratio ?? 0) >= 0.6}
          />
          <EvidenceItem
            label="行情拆分"
            value={summary.regime_evidence_sufficient ? "证据足够" : "证据不足"}
            passed={Boolean(summary.regime_evidence_sufficient)}
          />
        </div>
      ) : null}

      {summary.weakest_evidence.length ? (
        <div className="mt-4 rounded-xl border border-amber-300/15 bg-amber-300/[0.06] p-3">
          <div className="text-xs font-medium text-amber-100">当前最弱证据</div>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-300">
            {summary.weakest_evidence.map((item) => (
              <li key={item}>· {item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {summary.locked_test_metrics &&
      Object.keys(summary.locked_test_metrics).length ? (
        <div className="mt-4">
          <div className="mb-2 text-xs font-medium text-slate-300">
            最终保留测试
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
            <EvidenceItem
              label="净收益"
              value={formatPercent(summary.locked_test_metrics.total_return)}
              passed={
                (summary.locked_test_metrics.total_return ?? -1) >= 0
              }
            />
            <EvidenceItem
              label="最大回撤"
              value={formatPercent(
                Math.abs(
                  summary.locked_test_metrics.max_drawdown ?? 0,
                ),
              )}
              passed={lockedPassed}
            />
            <EvidenceItem
              label="盈亏效率"
              value={formatNumber(
                summary.locked_test_metrics.profit_factor,
              )}
              passed={
                (summary.locked_test_metrics.profit_factor ?? 0) >= 1
              }
            />
            <EvidenceItem
              label="单笔期望"
              value={formatNumber(
                summary.locked_test_metrics.expectancy,
                5,
              )}
              passed={
                (summary.locked_test_metrics.expectancy ?? -1) >= 0
              }
            />
            <EvidenceItem
              label="交易次数"
              value={formatInteger(
                summary.locked_test_metrics.trade_count,
              )}
              passed={
                (summary.locked_test_metrics.trade_count ?? 0) > 0
              }
            />
          </div>
        </div>
      ) : null}

      <div className="mt-4 rounded-xl bg-white/[0.03] p-3 text-sm leading-6 text-slate-300">
        <span className="font-medium text-emerald-100">下一步：</span>
        {summary.next_action}
      </div>
      {ready &&
      !summary.locked_test_used &&
      !lockedRunning &&
      onLaunchLockedTest ? (
        <div className="mt-4 rounded-xl border border-amber-300/20 bg-amber-300/[0.06] p-3">
          <div className="text-xs leading-5 text-amber-100">
            这会查看一次此前保留、未参与调参的最终区间。查看后不能再针对该区间修改参数，否则必须废弃本次结论并建立新的保留期。
          </div>
          <button
            type="button"
            onClick={onLaunchLockedTest}
            disabled={lockedTestPending}
            className="mt-3 min-h-11 w-full rounded-xl bg-amber-200 px-4 text-sm font-semibold text-amber-950 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          >
            {lockedTestPending
              ? "正在批准并排队…"
              : "批准并运行最终保留测试"}
          </button>
        </div>
      ) : null}
      <p className="mt-3 text-xs leading-5 text-slate-500">
        最终保留测试从不自动启动，也不会用于参数搜索；通过仍不代表可实盘或未来盈利。
      </p>

      <TechnicalDetails label="查看候选验证技术记录">
        {summary.job_id ? (
          <TechnicalId label="后台任务" value={summary.job_id} />
        ) : null}
        {summary.report_id ? (
          <TechnicalId label="验证报告" value={summary.report_id} />
        ) : null}
        {summary.report_artifact_key ? (
          <TechnicalId
            label="报告文件"
            value={summary.report_artifact_key}
          />
        ) : null}
        {summary.locked_test_job_id ? (
          <TechnicalId
            label="最终保留测试任务"
            value={summary.locked_test_job_id}
          />
        ) : null}
        {summary.locked_test_report_id ? (
          <TechnicalId
            label="最终保留测试报告"
            value={summary.locked_test_report_id}
          />
        ) : null}
      </TechnicalDetails>
    </section>
  );
}

function formatPercent(value: number | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
}

function formatNumber(value: number | undefined, digits = 3) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

function formatInteger(value: number | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString("zh-CN");
}

function EvidenceItem({
  label,
  value,
  passed,
}: {
  label: string;
  value: string;
  passed: boolean;
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-black/10 p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div
        className={
          passed
            ? "mt-1 text-sm font-medium text-emerald-200"
            : "mt-1 text-sm font-medium text-amber-200"
        }
      >
        {value}
      </div>
    </div>
  );
}
