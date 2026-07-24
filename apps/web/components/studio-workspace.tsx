"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { z } from "zod";

import {
  AgentStatus,
  apiFetch,
  AuditEvent,
  ComponentCandidate,
  ComponentEvidence,
  GateEvaluation,
  ImprovementDirection,
  ExperimentPlan,
  Trial,
  BatchSummary,
  Job,
  PipelineProfile,
  ProjectStatus,
  RegimeValidation,
  ResearchBudget,
  ResearchBudgetPolicy,
  ResearchHandoff,
  Session,
  StrategyOutcome,
  StrategyDraft,
  VersioningPolicy,
} from "@/lib/api";

const intakeSchema = z.object({
  title: z.string().trim().min(1, "请输入研究会话标题").max(160),
  sourceType: z.enum(["natural_language", "pine", "file"]),
  content: z.string().trim().min(1, "请输入策略原文或 Pine Script").max(500_000),
});

type BaselineVersion = {
  id: string;
  strategy_id: string;
  version: number;
  status: string;
  immutable: boolean;
};

export function StudioWorkspace() {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("ETH 永续策略研究");
  const [sourceType, setSourceType] = useState<"natural_language" | "pine" | "file">(
    "natural_language",
  );
  const [content, setContent] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [draft, setDraft] = useState<StrategyDraft | null>(null);
  const [baseline, setBaseline] = useState<BaselineVersion | null>(null);
  const [pipelineProfileId, setPipelineProfileId] = useState("fast_screen");
  const [notice, setNotice] = useState(
    "可输入下一份策略或新研究假设。当前已保留一份 rejected 策略及 diagnostic component 证据；AI Provider 未配置。",
  );

  const project = useQuery({
    queryKey: ["project-status"],
    queryFn: () => apiFetch<ProjectStatus>("/api/project/status"),
  });
  const agent = useQuery({
    queryKey: ["agent-status"],
    queryFn: () => apiFetch<AgentStatus>("/api/agent/status"),
  });
  const events = useQuery({
    queryKey: ["audit-events"],
    queryFn: () => apiFetch<AuditEvent[]>("/api/audit/events?limit=20"),
  });
  const sessions = useQuery({
    queryKey: ["research-sessions"],
    queryFn: () => apiFetch<Session[]>("/api/research/sessions"),
  });
  const activeSession = session ?? sessions.data?.[0] ?? null;
  const handoff = useQuery({
    queryKey: ["research-handoff", activeSession?.id],
    queryFn: () =>
      apiFetch<ResearchHandoff>(`/api/research/sessions/${activeSession?.id}/handoff`),
    enabled: Boolean(activeSession?.id),
    retry: false,
  });
  const versioning = useQuery({
    queryKey: ["versioning-policy"],
    queryFn: () => apiFetch<VersioningPolicy>("/api/versioning/policy"),
  });
  const profiles = useQuery({
    queryKey: ["pipeline-profiles"],
    queryFn: () => apiFetch<PipelineProfile[]>("/api/pipeline-profiles"),
  });
  const gates = useQuery({
    queryKey: ["gate-results"],
    queryFn: () => apiFetch<GateEvaluation[]>("/api/gates/results"),
  });
  const outcomes = useQuery({
    queryKey: ["strategy-outcomes"],
    queryFn: () => apiFetch<StrategyOutcome[]>("/api/strategy-outcomes"),
  });
  const components = useQuery({
    queryKey: ["component-candidates"],
    queryFn: () => apiFetch<ComponentCandidate[]>("/api/component-candidates"),
  });
  const componentEvidence = useQuery({
    queryKey: ["component-evidence"],
    queryFn: () => apiFetch<ComponentEvidence[]>("/api/component-evidence"),
  });
  const regimes = useQuery({
    queryKey: ["regime-validations"],
    queryFn: () => apiFetch<RegimeValidation[]>("/api/regime-validations"),
  });
  const budgetPolicy = useQuery({
    queryKey: ["research-budget-policy"],
    queryFn: () => apiFetch<ResearchBudgetPolicy>("/api/research-budget/default"),
  });
  const directions = useQuery({
    queryKey: ["improvement-directions"],
    queryFn: () => apiFetch<ImprovementDirection[]>("/api/improvement-directions"),
  });
  const plans = useQuery({
    queryKey: ["experiment-plans"],
    queryFn: () => apiFetch<ExperimentPlan[]>("/api/experiment-plans"),
  });
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => apiFetch<Job[]>("/api/jobs"),
    refetchInterval: 3000,
  });
  const activePlan = plans.data?.find((plan) => plan.proposal_id) ?? plans.data?.[0];
  const trials = useQuery({
    queryKey: ["trials", activePlan?.id],
    queryFn: () =>
      apiFetch<Trial[]>(`/api/experiment-plans/${activePlan?.id}/trials`),
    enabled: Boolean(activePlan?.id),
    refetchInterval: 3000,
  });
  const batchSummary = useQuery({
    queryKey: ["batch-summary", activePlan?.id],
    queryFn: () =>
      apiFetch<BatchSummary>(`/api/experiment-plans/${activePlan?.id}/batch-summary`),
    enabled: Boolean(activePlan?.id),
    refetchInterval: 3000,
  });
  const sessionBudget = useQuery({
    queryKey: ["research-budget", activeSession?.id],
    queryFn: () =>
      apiFetch<ResearchBudget>(`/api/research/sessions/${activeSession?.id}/budget`),
    enabled: Boolean(activeSession?.id),
  });
  const selectedProfile = profiles.data?.find((profile) => profile.id === pipelineProfileId);
  const latestViability = gates.data?.find((gate) => gate.gate_name === "viability");
  const latestOutcome = outcomes.data?.[0];
  const activeBatchJob = jobs.data?.find(
    (job) =>
      job.job_type === "parameter_search" &&
      job.payload.experiment_plan_id === activePlan?.id,
  );

  const approveDirection = useMutation({
    mutationFn: (proposalId: string) =>
      apiFetch(`/api/improvement-directions/${proposalId}/approve`, {
        method: "POST",
        body: JSON.stringify({
          subject_id: proposalId,
          confirmed_by_user: true,
        }),
      }),
    onSuccess: () => {
      setNotice("已按明确 subject_id 批准方向，并创建不可变 Candidate snapshot；Baseline 未覆盖。");
      queryClient.invalidateQueries({ queryKey: ["improvement-directions"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      queryClient.invalidateQueries({ queryKey: ["research-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
    },
  });
  const submitDirection = useMutation({
    mutationFn: (proposalId: string) =>
      apiFetch(`/api/improvement-directions/${proposalId}/submit`, {
        method: "POST",
      }),
    onSuccess: () => {
      setNotice("方向已提交等待审批；批准按钮将始终携带该 Proposal 的精确 subject_id。");
      queryClient.invalidateQueries({ queryKey: ["improvement-directions"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
    },
  });
  const reviseDirectionBudget = useMutation({
    mutationFn: ({ proposalId, trials, minutes }: { proposalId: string; trials: number; minutes: number }) =>
      apiFetch(`/api/improvement-directions/${proposalId}/budget`, {
        method: "POST",
        body: JSON.stringify({
          subject_id: proposalId,
          estimated_trials: trials,
          estimated_minutes: minutes,
        }),
      }),
    onSuccess: () => {
      setNotice("预算已按明确 subject_id 调整；如原来等待审批，状态已退回 draft 以便重新审阅。");
      queryClient.invalidateQueries({ queryKey: ["improvement-directions"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });
  const cancelBatchJob = useMutation({
    mutationFn: (jobId: string) =>
      apiFetch(`/api/jobs/${jobId}/cancel`, {
        method: "POST",
        body: JSON.stringify({ subject_id: jobId, confirmed_by_user: true }),
      }),
    onSuccess: () => {
      setNotice("已记录精确 Job subject 的取消请求；当前 Trial 证据保留，Runner 在安全批次边界停止。");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });

  const saveIntake = useMutation({
    mutationFn: async () => {
      const values = intakeSchema.parse({ title, sourceType, content });
      const activeSession =
        session ??
        (await apiFetch<Session>("/api/research/sessions", {
          method: "POST",
          body: JSON.stringify({ title: values.title }),
        }));
      if (!session) setSession(activeSession);
      await apiFetch(`/api/research/sessions/${activeSession.id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: values.content }),
      });
      return apiFetch<StrategyDraft>(
        `/api/research/sessions/${activeSession.id}/intakes`,
        {
          method: "POST",
          body: JSON.stringify({
            source_type: values.sourceType,
            source_name: values.sourceType === "file" ? "file-intake-placeholder" : null,
            raw_content: values.content,
          }),
        },
      );
    },
    onSuccess: (created) => {
      setDraft(created);
      setBaseline(null);
      setNotice(
        "原始来源已保存为 draft。未运行AI形式化；请先审阅原文和歧义，再人工冻结 baseline。",
      );
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      queryClient.invalidateQueries({ queryKey: ["research-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
    },
  });

  const freeze = useMutation({
    mutationFn: () =>
      apiFetch<BaselineVersion>(`/api/strategy-drafts/${draft?.id}/freeze-baseline`, {
        method: "POST",
        body: JSON.stringify({ confirmed_by_user: true, subject_id: draft?.id }),
      }),
    onSuccess: (version) => {
      setBaseline(version);
      setDraft((current) =>
        current
          ? { ...current, status: "baseline_frozen", baseline_version_id: version.id }
          : current,
      );
      setNotice("baseline v0 已冻结且不可覆盖。后续修改必须创建 Proposal/新版本。");
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      queryClient.invalidateQueries({ queryKey: ["research-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
    },
  });

  const formError = useMemo(() => {
    const error = saveIntake.error ?? freeze.error ?? submitDirection.error ?? approveDirection.error ?? reviseDirectionBudget.error ?? cancelBatchJob.error;
    if (error instanceof z.ZodError) return error.issues[0]?.message;
    return error instanceof Error ? error.message : null;
  }, [approveDirection.error, cancelBatchJob.error, freeze.error, reviseDirectionBudget.error, saveIntake.error, submitDirection.error]);

  return (
    <div className="min-h-screen p-4 md:p-6">
      <header className="mb-5 flex flex-col gap-4 border-b border-white/10 pb-5 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-300">
            AI Strategy Research Studio
          </div>
          <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">
            对话提出想法，结构化成果接受审计
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            External Agent 是阶段0优先执行者；Web展示相同的计划、工具调用、审批和成果记录，不隐藏AI动作。
          </p>
        </div>
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <Status label="Agent Provider" value="external_local_agent" tone="green" />
          <Status label="Execution Target" value="local_runtime" tone="green" />
          <Status
            label="Local Agent"
            value={agent.data?.external_agent.connection_status ?? "读取中"}
            tone="amber"
          />
          <Status
            label="Embedded Provider"
            value={project.data?.ai_provider.configured ? "configured" : "未配置"}
            tone="amber"
          />
        </div>
      </header>

      <section
        data-testid="research-handoff-card"
        className="mb-5 rounded-2xl border border-amber-300/25 bg-amber-300/[0.07] p-4 md:p-5"
      >
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-200">
              为什么停在这里 / 下一步
            </div>
            <div className="mt-2 text-base font-medium text-slate-100">
              {handoff.data?.stop_reason_text ?? "当前会话尚无结构化 Stop/Handoff。"}
            </div>
            <div className="mt-2 text-sm leading-6 text-slate-300">
              下一步：{handoff.data?.next_recommended_action ?? "保存策略后，系统会明确显示停止原因与下一动作。"}
            </div>
          </div>
          <div className="shrink-0 rounded-lg border border-white/10 bg-black/10 px-3 py-2 text-xs text-slate-300">
            {handoff.data?.status ?? "not_recorded"}
          </div>
        </div>
        {handoff.data ? (
          <div className="mt-4 grid gap-4 text-xs md:grid-cols-3">
            <div>
              <div className="mb-1 font-medium text-emerald-200">已完成</div>
              <ul className="space-y-1 text-slate-400">
                {handoff.data.completed_actions.map((item) => <li key={item}>· {item}</li>)}
              </ul>
            </div>
            <div>
              <div className="mb-1 font-medium text-slate-200">未执行</div>
              <ul className="space-y-1 text-slate-500">
                {handoff.data.not_started_actions.map((item) => <li key={item}>· {item}</li>)}
              </ul>
            </div>
            <div>
              <div className="mb-1 font-medium text-amber-200">需要用户操作</div>
              <div className="leading-5 text-slate-400">
                {handoff.data.user_action_required
                  ? handoff.data.required_user_action
                  : "当前不需要用户操作。"}
              </div>
              <div className="mt-2 break-all text-slate-500">
                subject: {handoff.data.approval_subject_id ?? handoff.data.subject_id}
              </div>
            </div>
          </div>
        ) : null}
        <div className="mt-4 border-t border-white/10 pt-3 text-xs text-slate-500">
          Versioning: {versioning.data?.available ? "Local authoritative / Git backup manual" : "policy unavailable"}
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)_360px]">
        <aside className="space-y-4">
          <Panel title="Research Session">
            <div className="space-y-2 text-sm">
              <Meta label="会话" value={activeSession?.title ?? "尚未创建"} />
              <Meta label="状态" value={activeSession?.status ?? "inbox"} />
              <Meta label="策略" value={draft ? "1 draft" : "0"} />
            </div>
          </Panel>
          <Panel title="Agent Runs">
            <div className="rounded-lg border border-dashed border-white/15 p-3 text-xs leading-5 text-slate-400">
              尚无 AgentRun。默认模式为 <span className="text-slate-200">guided</span>；读取和安全研究任务可自动，冻结与参数搜索必须审批。
            </div>
          </Panel>
          <Panel title="Research Budget">
            {sessionBudget.data ? (
              <div className="space-y-2 text-xs">
                <Meta
                  label="Hypotheses"
                  value={`${sessionBudget.data.remaining_hypotheses} / ${sessionBudget.data.max_hypotheses} remaining`}
                />
                <Meta
                  label="Trials"
                  value={`${sessionBudget.data.remaining_trials} / ${sessionBudget.data.max_trials_total} remaining`}
                />
                <Meta
                  label="Compute"
                  value={`${sessionBudget.data.remaining_compute_minutes} min remaining`}
                />
                <Meta
                  label="Locked test"
                  value={`${sessionBudget.data.remaining_locked_test_uses} uses remaining`}
                />
              </div>
            ) : (
              <Empty>
                默认预算：{budgetPolicy.data?.max_hypotheses ?? "—"} hypotheses / {budgetPolicy.data?.max_trials_total ?? "—"} trials；创建会话后显示已用与剩余。
              </Empty>
            )}
          </Panel>
          <Panel title="Batch Progress">
            {activePlan ? (
              <div className="space-y-2 text-xs">
                <Meta label="Job" value={activeBatchJob?.status ?? "not queued"} />
                <Meta
                  label="Trials"
                  value={`${trials.data?.filter((item) => ["succeeded", "failed", "cancelled"].includes(item.status)).length ?? 0} / ${activePlan.max_trials ?? 0}`}
                />
                <Meta
                  label="Concurrency"
                  value={String(activeBatchJob?.payload.max_concurrent_trials ?? "policy/auto")}
                />
                <Meta
                  label="Peak RSS"
                  value={`${Math.max(0, ...(trials.data ?? []).map((item) => item.peak_rss_mb ?? 0)).toFixed(1)} MB`}
                />
                <Meta
                  label="Stop reason"
                  value={activeBatchJob?.error ?? "—"}
                />
                {activeBatchJob && ["queued", "running"].includes(activeBatchJob.status) ? (
                  <button
                    type="button"
                    onClick={() => cancelBatchJob.mutate(activeBatchJob.id)}
                    disabled={cancelBatchJob.isPending}
                    className="w-full rounded-lg border border-rose-300/25 bg-rose-300/10 px-2 py-2 text-rose-100 disabled:opacity-40"
                  >
                    取消 subject {activeBatchJob.id.slice(0, 14)}
                  </button>
                ) : null}
              </div>
            ) : (
              <Empty>批准具体方向和预算后才会出现批量 Trial；页面只轮询结构化进度。</Empty>
            )}
          </Panel>
          <Panel title="Audit Timeline">
            <div className="max-h-72 space-y-3 overflow-auto pr-1">
              {events.data?.length ? (
                events.data.map((event) => (
                  <div key={event.id} className="border-l border-emerald-300/25 pl-3 text-xs">
                    <div className="text-slate-200">{event.event_type}</div>
                    <div className="mt-1 text-slate-500">{event.actor_type}</div>
                  </div>
                ))
              ) : (
                <div className="text-xs text-slate-500">关键动作将在这里按追加顺序出现。</div>
              )}
            </div>
          </Panel>
        </aside>

        <section className="flex min-h-[680px] flex-col rounded-2xl border border-white/10 bg-[#0a151e]/90">
          <div className="border-b border-white/10 px-5 py-4">
            <div className="text-sm font-semibold">策略对话与原始输入</div>
            <div className="mt-1 text-xs text-slate-500">消息是入口，结构化状态和审计事件才是权威记录。</div>
          </div>
          <div className="flex-1 space-y-4 p-5">
            <div className="max-w-[88%] rounded-2xl rounded-tl-sm border border-emerald-300/15 bg-emerald-300/[0.06] p-4 text-sm leading-6 text-slate-300">
              描述策略规则、粘贴 Pine Script，或预留文件来源。阶段0会保存原始内容，不会伪造AI分析结果。
            </div>
            {draft ? (
              <div className="ml-auto max-w-[88%] rounded-2xl rounded-tr-sm bg-slate-100 p-4 text-sm leading-6 text-slate-900">
                {draft.raw_content}
              </div>
            ) : null}
            <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3 text-xs leading-5 text-slate-400">
              {notice}
            </div>
          </div>
          <form
            className="border-t border-white/10 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              saveIntake.mutate();
            }}
          >
            <div className="mb-3 grid gap-3 sm:grid-cols-[1fr_180px]">
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                className="rounded-xl border border-white/10 bg-[#071017] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600"
                placeholder="研究会话标题"
              />
              <select
                value={sourceType}
                onChange={(event) => setSourceType(event.target.value as typeof sourceType)}
                className="rounded-xl border border-white/10 bg-[#071017] px-3 py-2.5 text-sm text-slate-200"
              >
                <option value="natural_language">自然语言</option>
                <option value="pine">Pine Script</option>
                <option value="file">文件来源占位</option>
              </select>
            </div>
            <textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              className="min-h-32 w-full resize-y rounded-xl border border-white/10 bg-[#071017] p-3 text-sm leading-6 text-slate-100 placeholder:text-slate-600"
              placeholder="输入原始策略，不要先为了盈利而改写……"
            />
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <div className="text-xs text-slate-500">
                文件上传接口尚未启用；不接受任意路径。
              </div>
              <button
                type="submit"
                disabled={saveIntake.isPending}
                className="rounded-xl bg-emerald-300 px-4 py-2.5 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {saveIntake.isPending ? "保存中…" : "保存为策略 Draft"}
              </button>
            </div>
            {formError ? <div className="mt-3 text-sm text-rose-300">{formError}</div> : null}
          </form>
        </section>

        <aside className="space-y-4">
          <Panel title="结构化策略">
            <div className="space-y-2 text-sm">
              <Meta label="Draft" value={draft?.id.slice(0, 18) ?? "未创建"} />
              <Meta label="来源" value={draft?.source_type ?? "—"} />
              <Meta label="状态" value={draft?.status ?? "—"} />
              <Meta label="AI形式化" value="未运行" />
            </div>
          </Panel>
          <Panel title="改进方向（最多 3 个）">
            {directions.data?.length ? (
              directions.data.slice(0, 3).map((direction) => (
                <div key={direction.id} className="mb-3 rounded-xl border border-white/10 p-3 text-xs last:mb-0">
                  <div className="font-medium leading-5 text-slate-100">{direction.hypothesis}</div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-slate-400">
                    <span>{direction.estimated_trials ?? "—"} Trials</span>
                    <span>{direction.estimated_minutes ?? "—"} min</span>
                    <span>{direction.parameter_space.length} parameters</span>
                    <span>{direction.evidence_refs.length} evidence refs</span>
                  </div>
                  <div className="mt-2 rounded-lg bg-white/[0.03] p-2 text-slate-500">
                    状态：{direction.status} · subject {direction.id.slice(0, 14)}
                  </div>
                  {direction.status === "waiting_approval" ? (
                    <button
                      type="button"
                      onClick={() => approveDirection.mutate(direction.id)}
                      disabled={approveDirection.isPending}
                      className="mt-2 w-full rounded-lg border border-emerald-300/30 bg-emerald-300/10 px-2 py-2 text-emerald-100 disabled:opacity-40"
                    >
                      批准 subject {direction.id.slice(0, 14)} 与预算
                    </button>
                  ) : null}
                  {direction.status === "draft" ? (
                    <button
                      type="button"
                      onClick={() => submitDirection.mutate(direction.id)}
                      disabled={submitDirection.isPending}
                      className="mt-2 w-full rounded-lg border border-sky-300/25 bg-sky-300/10 px-2 py-2 text-sky-100 disabled:opacity-40"
                    >
                      提交 subject {direction.id.slice(0, 14)} 审批
                    </button>
                  ) : null}
                  {direction.status === "draft" || direction.status === "waiting_approval" ? (
                    <form
                      className="mt-2 grid grid-cols-[1fr_1fr_auto] gap-1"
                      onSubmit={(event) => {
                        event.preventDefault();
                        const form = new FormData(event.currentTarget);
                        reviseDirectionBudget.mutate({
                          proposalId: direction.id,
                          trials: Number(form.get("trials")),
                          minutes: Number(form.get("minutes")),
                        });
                      }}
                    >
                      <input
                        name="trials"
                        type="number"
                        min={1}
                        defaultValue={direction.estimated_trials ?? 20}
                        aria-label="Trial budget"
                        className="min-w-0 rounded-md border border-white/10 bg-[#071017] px-2 py-1.5 text-slate-200"
                      />
                      <input
                        name="minutes"
                        type="number"
                        min={1}
                        defaultValue={direction.estimated_minutes ?? 45}
                        aria-label="Time budget minutes"
                        className="min-w-0 rounded-md border border-white/10 bg-[#071017] px-2 py-1.5 text-slate-200"
                      />
                      <button type="submit" className="rounded-md border border-white/10 px-2 text-slate-300">
                        调整
                      </button>
                    </form>
                  ) : null}
                </div>
              ))
            ) : (
              <Empty>
                Provider 未配置，不伪造 AI 建议。人工或 External Agent 可通过同一 API 创建结构化方向。
              </Empty>
            )}
          </Panel>
          <Panel title="Experiment Plan">
            {activePlan ? (
              <div className="space-y-2 text-xs">
                <Meta label="Status" value={activePlan.status} />
                <Meta label="Search" value={`${activePlan.search_strategy} / seed ${activePlan.random_seed}`} />
                <Meta label="Budget" value={`${activePlan.max_trials ?? "—"} Trials / ${Math.ceil((activePlan.time_budget_seconds ?? 0) / 60)} min`} />
                <Meta label="Split" value="train + validation only" />
                <div className="leading-5 text-slate-500">Locked test 仅保留给少量候选，禁止参与参数搜索。</div>
              </div>
            ) : (
              <Empty>方向批准后创建计划；预算、切分、成本、目标和停止条件缺一不可。</Empty>
            )}
          </Panel>
          <Panel title="Research Pipeline">
            <label className="mb-2 block text-xs text-slate-500" htmlFor="pipeline-profile">
              Pipeline Profile
            </label>
            <select
              id="pipeline-profile"
              value={pipelineProfileId}
              onChange={(event) => setPipelineProfileId(event.target.value)}
              className="mb-3 w-full rounded-lg border border-white/10 bg-[#071017] px-3 py-2 text-xs text-slate-200"
            >
              {(profiles.data ?? []).map((profile) => (
                <option key={profile.id} value={profile.id}>{profile.label}</option>
              ))}
            </select>
            <div className="space-y-2">
              {(selectedProfile?.stages ?? []).map((stage, index) => {
                const result = gates.data?.find((gate) => gate.gate_name === stage.gate);
                return (
                  <div key={stage.id} className="flex gap-3 text-xs">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-white/15 text-[10px] text-slate-400">
                      {index + 1}
                    </span>
                    <div>
                      <div className="text-slate-200">{stage.id}</div>
                      <div className="text-slate-500">
                        {result?.status ?? "not_evaluated"} · fail-fast {stage.stop_on_fail ? "on" : "off"}
                      </div>
                    </div>
                  </div>
                );
              })}
              {!selectedProfile ? <Empty>Pipeline Profile 读取中。</Empty> : null}
            </div>
          </Panel>
          <Panel title="Viability & Outcome">
            {latestViability ? (
              <div className="space-y-2 text-xs">
                <Meta label="Viability" value={latestViability.status} />
                <div className="leading-5 text-slate-400">{latestViability.reasons.join("；")}</div>
              </div>
            ) : (
              <Empty>尚无 viability 结果；“只比 baseline 少亏”不会成为完整策略 candidate。</Empty>
            )}
            <div className="mt-3 border-t border-white/[0.06] pt-3 text-xs text-slate-400">
              Strategy outcome：<span className="text-slate-200">{latestOutcome?.outcome_type ?? "未记录"}</span>
            </div>
          </Panel>
          <Panel title="Batch Result Summary">
            {batchSummary.data?.trial_count ? (
              <div className="space-y-2 text-xs">
                <Meta
                  label="Evidence"
                  value={batchSummary.data.evidence_mode}
                />
                <Meta label="Succeeded" value={`${batchSummary.data.succeeded_count}/${batchSummary.data.trial_count}`} />
                <Meta label="Stable Trials" value={String(batchSummary.data.stable_count)} />
                <Meta
                  label="Val Net"
                  value={String(batchSummary.data.representative_stable_metrics.validation_net_return ?? "—")}
                />
                <Meta
                  label="PF / Expectancy"
                  value={`${batchSummary.data.representative_stable_metrics.validation_profit_factor ?? "—"} / ${batchSummary.data.representative_stable_metrics.validation_expectancy ?? "—"}`}
                />
                <Meta
                  label="Trades / Drawdown"
                  value={`${batchSummary.data.representative_stable_metrics.validation_trade_count ?? "—"} / ${batchSummary.data.representative_stable_metrics.validation_max_drawdown_abs ?? "—"}`}
                />
                <div className="rounded-lg bg-white/[0.03] p-2 leading-5 text-slate-400">
                  稳定区间：{JSON.stringify(batchSummary.data.stable_parameter_ranges)}
                </div>
                <div className="text-slate-500">
                  Baseline 指标尚未连接时明确显示缺失；成本模型、Regime 与失败原因从 Plan/Evidence 读取，不静默补造。孤立最高点不会自动成为 Candidate。
                </div>
                {!batchSummary.data.research_conclusion_allowed ? (
                  <div className="rounded-lg border border-amber-300/20 bg-amber-300/10 p-2 leading-5 text-amber-100">
                    当前是 fixture/不可用证据，只验证批量执行连线；这些数值不能形成 Candidate、收益或稳定性结论。
                  </div>
                ) : null}
              </div>
            ) : (
              <Empty>尚无真实批量结果。测试 fixture 必须明确标记，不能伪装成盈利候选。</Empty>
            )}
          </Panel>
          <Panel title="Failure → Component Branch">
            {components.data?.length ? (
              components.data.slice(0, 3).map((component) => (
                <div key={component.id} className="mb-2 rounded-lg border border-white/10 p-2 text-xs last:mb-0">
                  <div className="text-slate-200">{component.name}</div>
                  <div className="mt-1 text-amber-200">{component.status}</div>
                  <div className="mt-1 text-slate-500">
                    {componentEvidence.data?.find((item) => item.id === component.evidence_id)
                      ?.out_of_sample_status ?? "evidence pending"}
                  </div>
                </div>
              ))
            ) : (
              <Empty>失败策略可做廉价归因；组件只能保留为 diagnostic/component candidate，不会自动成为 validated factor。</Empty>
            )}
          </Panel>
          <Panel title="Regime & Pine Stage">
            {regimes.data?.length ? (
              <div className="space-y-2 text-xs">
                <Meta label="Evidence" value={regimes.data[0].evidence_status} />
                <Meta label="Mode" value={regimes.data[0].mode} />
                <Meta label="Unknown" value={regimes.data[0].unknown_regimes.join(", ") || "none"} />
              </div>
            ) : (
              <Empty>
                Viability 前只允许 regime_diagnostic，结论只能是 diagnostic/screening；
                正式 regime_validation 必须引用同一 subject 的 passed viability。
              </Empty>
            )}
            <div className="mt-3 border-t border-white/[0.06] pt-3 text-xs leading-5 text-slate-400">
              Pine 来源先检查 semantic / repainting / MTF 和少量 golden trades；完整诊断在 fast screen 与 viability 通过后。
            </div>
          </Panel>
          <Panel title="审批与成果">
            <div className="mb-3 text-xs leading-5 text-slate-400">
              baseline 冻结后不可覆盖；策略 diff、实验、Trial 与报告将作为独立 Artifact 展示。
            </div>
            <button
              type="button"
              disabled={!draft || Boolean(draft.baseline_version_id) || freeze.isPending}
              onClick={() => freeze.mutate()}
              className="w-full rounded-xl border border-emerald-300/30 bg-emerald-300/10 px-3 py-2.5 text-sm font-medium text-emerald-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {baseline
                ? "baseline v0 已冻结"
                : `确认 subject ${draft?.id?.slice(0, 16) ?? "—"} 并冻结 baseline v0`}
            </button>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-white/15 p-3 text-xs leading-5 text-slate-500">
      {children}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-[#0a151e]/90 p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{title}</h2>
      {children}
    </section>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-white/[0.06] pb-2 last:border-0 last:pb-0">
      <span className="text-slate-500">{label}</span>
      <span className="max-w-[65%] break-all text-right text-slate-200">{value}</span>
    </div>
  );
}

function Status({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "green" | "amber";
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2">
      <div className="text-slate-500">{label}</div>
      <div className={tone === "green" ? "mt-1 text-emerald-200" : "mt-1 text-amber-200"}>
        {value}
      </div>
    </div>
  );
}
