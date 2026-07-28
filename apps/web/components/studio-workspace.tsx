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
  ComponentHypothesis,
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
  ResearchAuthorization,
  ResearchAuthorizationStage,
  ResearchDiagnosticReport,
  ResearchHandoff,
  RunBundle,
  Session,
  SessionDetail,
  ResearchModeDefinition,
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
  const sessionDetail = useQuery({
    queryKey: ["research-session-detail", activeSession?.id],
    queryFn: () =>
      apiFetch<SessionDetail>(`/api/research/sessions/${activeSession?.id}`),
    enabled: Boolean(activeSession?.id),
  });
  const activeDraft =
    draft?.session_id === activeSession?.id
      ? draft
      : sessionDetail.data?.drafts[0] ?? null;
  const currentBaselineId =
    baseline && activeDraft?.baseline_version_id === baseline.id
      ? baseline.id
      : activeDraft?.baseline_version_id ?? null;
  const researchModes = useQuery({
    queryKey: ["research-modes"],
    queryFn: () => apiFetch<ResearchModeDefinition[]>("/api/research-modes"),
  });
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
    queryKey: ["gate-results", currentBaselineId],
    queryFn: () =>
      apiFetch<GateEvaluation[]>(
        `/api/gates/results?subject_id=${encodeURIComponent(currentBaselineId ?? "")}`,
      ),
    enabled: Boolean(currentBaselineId),
  });
  const outcomes = useQuery({
    queryKey: ["strategy-outcomes", currentBaselineId],
    queryFn: () =>
      apiFetch<StrategyOutcome[]>(
        `/api/strategy-outcomes?strategy_version_id=${encodeURIComponent(currentBaselineId ?? "")}`,
      ),
    enabled: Boolean(currentBaselineId),
  });
  const latestOutcome = outcomes.data?.[0];
  const currentSubjectId =
    currentBaselineId ??
    (sessionDetail.isSuccess ? handoff.data?.subject_id ?? null : null);
  const authorizations = useQuery({
    queryKey: ["research-authorizations", currentSubjectId],
    queryFn: () =>
      apiFetch<ResearchAuthorization[]>(
        `/api/research-authorizations?subject_id=${encodeURIComponent(currentSubjectId ?? "")}`,
      ),
    enabled: Boolean(currentSubjectId),
  });
  const activeAuthorization = authorizations.data?.[0];
  const authorizationStages = useQuery({
    queryKey: ["research-authorization-stages", activeAuthorization?.id],
    queryFn: () =>
      apiFetch<ResearchAuthorizationStage[]>(
        `/api/research-authorizations/${activeAuthorization?.id}/stages`,
      ),
    enabled: Boolean(activeAuthorization?.id),
    refetchInterval: activeAuthorization?.status === "active" ? 3000 : false,
  });
  const runBundles = useQuery({
    queryKey: ["run-bundles", currentSubjectId],
    queryFn: () =>
      apiFetch<RunBundle[]>(
        `/api/run-bundles?subject_id=${encodeURIComponent(currentSubjectId ?? "")}`,
      ),
    enabled: Boolean(currentSubjectId),
  });
  const diagnosticReport = useQuery({
    queryKey: ["research-diagnostic", currentSubjectId],
    queryFn: () =>
      apiFetch<ResearchDiagnosticReport>(
        `/api/research-diagnostics/latest?subject_id=${encodeURIComponent(currentSubjectId ?? "")}`,
      ),
    enabled: Boolean(currentSubjectId),
    retry: false,
  });
  const components = useQuery({
    queryKey: ["component-candidates", currentBaselineId],
    queryFn: () =>
      apiFetch<ComponentCandidate[]>(
        `/api/component-candidates?source_strategy_version_id=${encodeURIComponent(currentBaselineId ?? "")}`,
      ),
    enabled: Boolean(currentBaselineId),
  });
  const componentEvidence = useQuery({
    queryKey: ["component-evidence", currentBaselineId],
    queryFn: () =>
      apiFetch<ComponentEvidence[]>(
        `/api/component-evidence?source_strategy_version_id=${encodeURIComponent(currentBaselineId ?? "")}`,
      ),
    enabled: Boolean(currentBaselineId),
  });
  const componentHypotheses = useQuery({
    queryKey: ["component-hypotheses", currentSubjectId],
    queryFn: () =>
      apiFetch<ComponentHypothesis[]>(
        `/api/component-hypotheses?subject_id=${encodeURIComponent(currentSubjectId ?? "")}`,
      ),
    enabled: Boolean(currentSubjectId),
  });
  const regimes = useQuery({
    queryKey: ["regime-validations", currentSubjectId],
    queryFn: () =>
      apiFetch<RegimeValidation[]>(
        `/api/regime-validations?subject_id=${encodeURIComponent(currentSubjectId ?? "")}`,
      ),
    enabled: Boolean(currentSubjectId),
  });
  const budgetPolicy = useQuery({
    queryKey: ["research-budget-policy"],
    queryFn: () => apiFetch<ResearchBudgetPolicy>("/api/research-budget/default"),
  });
  const directions = useQuery({
    queryKey: ["improvement-directions", currentBaselineId],
    queryFn: () =>
      apiFetch<ImprovementDirection[]>(
        `/api/improvement-directions?baseline_version_id=${encodeURIComponent(currentBaselineId ?? "")}`,
      ),
    enabled: Boolean(currentBaselineId),
  });
  const plans = useQuery({
    queryKey: ["experiment-plans", currentBaselineId],
    queryFn: () =>
      apiFetch<ExperimentPlan[]>(
        `/api/experiment-plans?baseline_version_id=${encodeURIComponent(currentBaselineId ?? "")}`,
      ),
    enabled: Boolean(currentBaselineId),
  });
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => apiFetch<Job[]>("/api/jobs"),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((job) =>
        ["queued", "running"].includes(job.status),
      )
        ? 3000
        : false,
  });
  const activePlan =
    plans.data?.find(
      (plan) =>
        plan.baseline_version_id === currentBaselineId &&
        Boolean(plan.proposal_id),
    ) ??
    plans.data?.find((plan) => plan.baseline_version_id === currentBaselineId);
  const activeBatchJob = jobs.data?.find(
    (job) =>
      job.job_type === "parameter_search" &&
      job.payload.experiment_plan_id === activePlan?.id,
  );
  const batchIsRunning = Boolean(
    activeBatchJob && ["queued", "running"].includes(activeBatchJob.status),
  );
  const trials = useQuery({
    queryKey: ["trials", activePlan?.id],
    queryFn: () =>
      apiFetch<Trial[]>(`/api/experiment-plans/${activePlan?.id}/trials`),
    enabled: Boolean(activePlan?.id),
    refetchInterval: batchIsRunning ? 3000 : false,
  });
  const batchSummary = useQuery({
    queryKey: ["batch-summary", activePlan?.id],
    queryFn: () =>
      apiFetch<BatchSummary>(`/api/experiment-plans/${activePlan?.id}/batch-summary`),
    enabled: Boolean(activePlan?.id),
    refetchInterval: batchIsRunning ? 3000 : false,
  });
  const sessionBudget = useQuery({
    queryKey: ["research-budget", activeSession?.id],
    queryFn: () =>
      apiFetch<ResearchBudget>(`/api/research/sessions/${activeSession?.id}/budget`),
    enabled: Boolean(activeSession?.id),
  });
  const selectedProfile = profiles.data?.find((profile) => profile.id === pipelineProfileId);
  const latestViability = gates.data?.find(
    (gate) =>
      gate.gate_name === "viability" && gate.subject_id === currentBaselineId,
  );
  const validationAttribution =
    diagnosticReport.data?.loss_attribution.splits.validation;
  const validationFunnel =
    diagnosticReport.data?.loss_attribution.signal_funnel.validation;
  const latestRegime = regimes.data?.[0];
  const regimeMetrics =
    diagnosticReport.data?.regime_diagnostic.regime_metrics ??
    latestRegime?.regime_metrics ??
    {};
  const regimeGroups =
    diagnosticReport.data?.regime_diagnostic.groups ??
    (latestRegime
      ? {
          suitable: latestRegime.suitable_regimes,
          conditional: latestRegime.conditional_regimes,
          blocked: latestRegime.blocked_regimes,
          unknown: latestRegime.unknown_regimes,
        }
      : { suitable: [], conditional: [], blocked: [], unknown: [] });
  const topConclusion = buildTopConclusion({
    hasBaseline: Boolean(currentBaselineId),
    outcome: latestOutcome?.outcome_type,
    viability: latestViability?.status,
    suitableRegimeCount: regimeGroups.suitable.length,
    regimeCount: Object.keys(regimeMetrics).length,
  });
  const scopedAggregateIds = new Set<string>();
  if (activeSession?.id) scopedAggregateIds.add(activeSession.id);
  if (activeDraft?.id) scopedAggregateIds.add(activeDraft.id);
  if (currentBaselineId) scopedAggregateIds.add(currentBaselineId);
  if (handoff.data?.id) scopedAggregateIds.add(handoff.data.id);
  for (const item of directions.data ?? []) scopedAggregateIds.add(item.id);
  for (const item of plans.data ?? []) scopedAggregateIds.add(item.id);
  for (const item of trials.data ?? []) scopedAggregateIds.add(item.id);
  for (const item of outcomes.data ?? []) scopedAggregateIds.add(item.id);
  for (const item of gates.data ?? []) scopedAggregateIds.add(item.id);
  for (const item of components.data ?? []) scopedAggregateIds.add(item.id);
  for (const item of componentEvidence.data ?? []) scopedAggregateIds.add(item.id);
  for (const item of componentHypotheses.data ?? []) scopedAggregateIds.add(item.id);
  for (const item of regimes.data ?? []) scopedAggregateIds.add(item.id);
  for (const item of authorizations.data ?? []) scopedAggregateIds.add(item.id);
  const scopedEvents = (events.data ?? []).filter((event) =>
    scopedAggregateIds.has(event.aggregate_id),
  );

  const updateResearchMode = useMutation({
    mutationFn: (mode: "quick" | "guided" | "expert") => {
      if (!activeSession?.id) throw new Error("请先创建或选择研究会话。");
      return apiFetch<Session>(
        `/api/research/sessions/${activeSession.id}/mode`,
        {
          method: "PUT",
          body: JSON.stringify({
            mode,
            confirmed_by_user: true,
          }),
        },
      );
    },
    onSuccess: (updated) => {
      setSession(updated);
      setNotice(
        `已切换为${researchModes.data?.find((item) => item.mode === updated.research_mode)?.label ?? updated.research_mode}。网页、本地 AI、CLI 和后续 AgentRun 将读取同一会话配置。`,
      );
      queryClient.invalidateQueries({ queryKey: ["research-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["research-session-detail"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });

  const authorizeToViability = useMutation({
    mutationFn: () => {
      if (!currentSubjectId || !activeSession?.id) {
        throw new Error("需要先选择明确的策略 subject 和研究会话。");
      }
      return apiFetch<ResearchAuthorization>("/api/research-authorizations", {
        method: "POST",
        body: JSON.stringify({
          subject_id: currentSubjectId,
          session_id: activeSession.id,
          allowed_stages: [
            "correctness",
            "smoke",
            "fast_screen",
            "viability",
            "loss_attribution",
            "regime_diagnostic",
            "component_hypothesis_generation",
          ],
          auto_continue: true,
          max_cost_usdt: 0,
          max_time_minutes: 45,
          max_trials: 0,
          locked_test_allowed: false,
          stop_conditions: [
            "gate_failed",
            "budget_exhausted",
            "blocked_dependency",
            "authorization_expired",
            "safety_boundary_reached",
          ],
          expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
          confirmed_by_user: true,
        }),
      });
    },
    onSuccess: () => {
      setNotice(
        "已一次授权 correctness → smoke → fast_screen → viability；内部阶段不再逐次询问，Gate失败或授权范围结束时才停止。",
      );
      queryClient.invalidateQueries({ queryKey: ["research-authorizations"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
    },
  });

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
      queryClient.invalidateQueries({ queryKey: ["research-session-detail"] });
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
      apiFetch<BaselineVersion>(`/api/strategy-drafts/${activeDraft?.id}/freeze-baseline`, {
        method: "POST",
        body: JSON.stringify({ confirmed_by_user: true, subject_id: activeDraft?.id }),
      }),
    onSuccess: (version) => {
      setBaseline(version);
      setDraft(
        activeDraft
          ? {
              ...activeDraft,
              status: "baseline_frozen",
              baseline_version_id: version.id,
            }
          : null,
      );
      setNotice("baseline v0 已冻结且不可覆盖。后续修改必须创建 Proposal/新版本。");
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      queryClient.invalidateQueries({ queryKey: ["research-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["research-session-detail"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
    },
  });

  const formError = useMemo(() => {
    const error =
      saveIntake.error ??
      freeze.error ??
      authorizeToViability.error ??
      submitDirection.error ??
      approveDirection.error ??
      reviseDirectionBudget.error ??
      cancelBatchJob.error ??
      updateResearchMode.error;
    if (error instanceof z.ZodError) return error.issues[0]?.message;
    return error instanceof Error ? error.message : null;
  }, [
    approveDirection.error,
    authorizeToViability.error,
    cancelBatchJob.error,
    freeze.error,
    reviseDirectionBudget.error,
    saveIntake.error,
    submitDirection.error,
    updateResearchMode.error,
  ]);

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
            本地 AI 是阶段0优先执行者；网页展示同一份计划、工具调用、审批和成果记录，不隐藏 AI 动作。
          </p>
        </div>
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <Status label="AI 执行方式" value="本地 Agent" tone="green" />
          <Status label="计算位置" value="本机运行" tone="green" />
          <Status
            label="本地 Agent 状态"
            value={cnStatus(agent.data?.external_agent.connection_status ?? "读取中")}
            tone="amber"
          />
          <Status
            label="网页内嵌模型"
            value={project.data?.ai_provider.configured ? "已配置" : "未配置"}
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
            {cnStatus(handoff.data?.status ?? "not_recorded")}
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
              <div className="mt-2 text-slate-500">
                当前对象：{shortId(handoff.data.approval_subject_id ?? handoff.data.subject_id)}
              </div>
            </div>
          </div>
        ) : null}
        <div className="mt-4 border-t border-white/10 pt-3 text-xs text-slate-500">
          版本规则：{versioning.data?.available ? "本地状态为准，Git 仅手动备份" : "规则暂不可用"}
        </div>
      </section>

      <section
        data-testid="strategy-conclusion-card"
        className={`mb-5 rounded-2xl border p-4 md:p-5 ${
          topConclusion.tone === "danger"
            ? "border-rose-300/30 bg-rose-300/[0.08]"
            : topConclusion.tone === "success"
              ? "border-emerald-300/30 bg-emerald-300/[0.08]"
              : "border-sky-300/20 bg-sky-300/[0.06]"
        }`}
      >
        <div className="text-xs font-semibold tracking-[0.16em] text-slate-400">
          当前策略结论
        </div>
        <div className="mt-2 text-lg font-semibold text-slate-100">
          {topConclusion.title}
        </div>
        <div className="mt-2 text-sm leading-6 text-slate-300">
          {topConclusion.detail}
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)_360px]">
        <aside className="space-y-4">
          <Panel title="研究会话">
            <div className="space-y-2 text-sm">
              <label className="block text-xs text-slate-500" htmlFor="session-select">
                当前会话
              </label>
              <select
                id="session-select"
                value={activeSession?.id ?? ""}
                onChange={(event) => {
                  const selected = sessions.data?.find(
                    (item) => item.id === event.target.value,
                  );
                  setSession(selected ?? null);
                  setDraft(null);
                  setBaseline(null);
                }}
                className="w-full rounded-lg border border-white/10 bg-[#071017] px-2 py-2 text-xs text-slate-200"
              >
                {(sessions.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
              <Meta label="状态" value={cnStatus(activeSession?.status ?? "inbox")} />
              <Meta label="策略草稿" value={activeDraft ? "1 份" : "0 份"} />
              <Meta label="当前基准" value={currentBaselineId ? shortId(currentBaselineId) : "未冻结"} />
            </div>
          </Panel>
          <Panel title="研究模式">
            <div className="space-y-2">
              {(researchModes.data ?? []).map((item) => {
                const selected = activeSession?.research_mode === item.mode;
                return (
                  <button
                    key={item.mode}
                    type="button"
                    onClick={() => updateResearchMode.mutate(item.mode)}
                    disabled={!activeSession || updateResearchMode.isPending}
                    className={`w-full rounded-xl border p-3 text-left text-xs transition ${
                      selected
                        ? "border-emerald-300/35 bg-emerald-300/10"
                        : "border-white/10 bg-white/[0.02] hover:border-white/20"
                    }`}
                  >
                    <div className="font-medium text-slate-100">{item.label}</div>
                    <div className="mt-1 leading-5 text-slate-400">{item.description}</div>
                  </button>
                );
              })}
              <div className="rounded-lg border border-dashed border-white/15 p-3 text-[11px] leading-5 text-slate-500">
                本地 AI 无需另选模式：启动 AgentRun 时自动读取当前会话的
                {activeSession?.research_mode
                  ? `“${cnResearchMode(activeSession.research_mode)}”`
                  : "模式"}
                。模式只减少机械停顿，不会跳过基准冻结、具体改动、批量预算、锁定测试或实盘门禁。
              </div>
            </div>
          </Panel>
          <Panel title="一次性研究授权">
            {activeAuthorization ? (
              <div className="space-y-2 text-xs">
                <Meta label="状态" value={cnStatus(activeAuthorization.status)} />
                <Meta
                  label="范围"
                  value={activeAuthorization.allowed_stages.map(cnStage).join(" → ")}
                />
                <Meta
                  label="耗时"
                  value={`${activeAuthorization.used_time_minutes.toFixed(1)} / ${activeAuthorization.max_time_minutes} 分钟`}
                />
                <div className="space-y-1 rounded-lg bg-white/[0.03] p-2 text-slate-400">
                  {(authorizationStages.data ?? []).map((item) => (
                    <div key={item.id}>
                      {cnStage(item.stage)}：<span className="text-slate-200">{cnStatus(item.status)}</span>
                    </div>
                  ))}
                  {!authorizationStages.data?.length ? (
                    <div>已授权；等待确定性执行适配器记录阶段进度。</div>
                  ) : null}
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <Empty>
                  一次授权安全范围后，correctness、smoke、fast_screen 与读取已保存指标的
                  viability 不再逐阶段询问。Gate 失败时自动停止。
                </Empty>
                <button
                  type="button"
                  onClick={() => authorizeToViability.mutate()}
                  disabled={
                    !currentSubjectId ||
                    !activeSession ||
                    latestOutcome?.outcome_type === "rejected" ||
                    authorizeToViability.isPending
                  }
                  className="w-full rounded-lg border border-emerald-300/30 bg-emerald-300/10 px-2 py-2 text-xs text-emerald-100 disabled:opacity-40"
                >
                  授权“研究到 Viability”
                </button>
                <div className="text-[11px] leading-4 text-slate-500">
                  UI 自动携带当前 subject；无需复制长 ID。Rejected 策略只能做已授权廉价诊断。
                </div>
              </div>
            )}
          </Panel>
          <Panel title="研究预算">
            {sessionBudget.data ? (
              <div className="space-y-2 text-xs">
                <Meta
                  label="改进假设"
                  value={`剩余 ${sessionBudget.data.remaining_hypotheses} / ${sessionBudget.data.max_hypotheses}`}
                />
                <Meta
                  label="参数方案"
                  value={`剩余 ${sessionBudget.data.remaining_trials} / ${sessionBudget.data.max_trials_total}`}
                />
                <Meta
                  label="计算时间"
                  value={`剩余 ${sessionBudget.data.remaining_compute_minutes} 分钟`}
                />
                <Meta
                  label="最终保留测试"
                  value={`剩余 ${sessionBudget.data.remaining_locked_test_uses} 次`}
                />
              </div>
            ) : (
              <Empty>
                默认预算：{budgetPolicy.data?.max_hypotheses ?? "—"} hypotheses / {budgetPolicy.data?.max_trials_total ?? "—"} trials；创建会话后显示已用与剩余。
              </Empty>
            )}
          </Panel>
          <Panel title="批量参数进度">
            {activePlan ? (
              <div className="space-y-2 text-xs">
                <Meta label="任务" value={cnStatus(activeBatchJob?.status ?? "not queued")} />
                <Meta
                  label="参数方案"
                  value={`${trials.data?.filter((item) => ["succeeded", "failed", "cancelled"].includes(item.status)).length ?? 0} / ${activePlan.max_trials ?? 0}`}
                />
                <Meta
                  label="并发数"
                  value={String(activeBatchJob?.payload.max_concurrent_trials ?? "policy/auto")}
                />
                <Meta
                  label="峰值内存"
                  value={`${Math.max(0, ...(trials.data ?? []).map((item) => item.peak_rss_mb ?? 0)).toFixed(1)} MB`}
                />
                <Meta
                  label="停止原因"
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
          <Panel title="研究结果包">
            {runBundles.data?.length ? (
              runBundles.data.slice(0, 4).map((bundle) => (
                <div
                  key={bundle.bundle_id}
                  className="mb-2 rounded-lg border border-white/10 p-2 text-xs last:mb-0"
                >
                  <div className="text-slate-200">{bundle.report_type}</div>
                  <div className="mt-1 text-slate-500">
                    {bundle.status} · {bundle.job_type}
                  </div>
                  <div className="mt-1 break-all text-slate-600">
                    {bundle.report_artifact_key}
                  </div>
                </div>
              ))
            ) : (
              <Empty>
                每次 Run 作为一个 Bundle 展示；manifest、关键指标和失败原因是权威证据，
                图表与详细日志可重建或归档。
              </Empty>
            )}
          </Panel>
          <Panel title="当前会话时间线">
            <div className="max-h-72 space-y-3 overflow-auto pr-1">
              {scopedEvents.length ? (
                scopedEvents.map((event) => (
                  <div key={event.id} className="border-l border-emerald-300/25 pl-3 text-xs">
                    <div className="text-slate-200">{cnEvent(event.event_type)}</div>
                    <div className="mt-1 text-slate-500">{cnActor(event.actor_type)}</div>
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
            {activeDraft ? (
              <div className="ml-auto max-w-[88%] rounded-2xl rounded-tr-sm bg-slate-100 p-4 text-sm leading-6 text-slate-900">
                {activeDraft.raw_content}
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
              <Meta label="策略草稿" value={activeDraft ? shortId(activeDraft.id) : "未创建"} />
              <Meta label="来源" value={cnSource(activeDraft?.source_type ?? "—")} />
              <Meta label="状态" value={cnStatus(activeDraft?.status ?? "—")} />
              <Meta label="AI 形式化" value="未运行" />
            </div>
          </Panel>
          <Panel title="改进方向（最多 3 个）">
            {directions.data?.length ? (
              directions.data.slice(0, 3).map((direction) => (
                <div key={direction.id} className="mb-3 rounded-xl border border-white/10 p-3 text-xs last:mb-0">
                  <div className="font-medium leading-5 text-slate-100">{direction.hypothesis}</div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-slate-400">
                    <span>{direction.estimated_trials ?? "—"} 个参数方案</span>
                    <span>{direction.estimated_minutes ?? "—"} 分钟</span>
                    <span>{direction.parameter_space.length} 个参数</span>
                    <span>{direction.evidence_refs.length} 条证据</span>
                  </div>
                  <div className="mt-2 rounded-lg bg-white/[0.03] p-2 text-slate-500">
                    状态：{cnStatus(direction.status)} · 对象 {shortId(direction.id)}
                  </div>
                  {direction.status === "waiting_approval" ? (
                    <button
                      type="button"
                      onClick={() => approveDirection.mutate(direction.id)}
                      disabled={approveDirection.isPending}
                      className="mt-2 w-full rounded-lg border border-emerald-300/30 bg-emerald-300/10 px-2 py-2 text-emerald-100 disabled:opacity-40"
                    >
                      批准这个方向与预算
                    </button>
                  ) : null}
                  {direction.status === "draft" ? (
                    <button
                      type="button"
                      onClick={() => submitDirection.mutate(direction.id)}
                      disabled={submitDirection.isPending}
                      className="mt-2 w-full rounded-lg border border-sky-300/25 bg-sky-300/10 px-2 py-2 text-sky-100 disabled:opacity-40"
                    >
                      提交这个方向审批
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
          <Panel title="实验计划">
            {activePlan ? (
              <div className="space-y-2 text-xs">
                <Meta label="状态" value={cnStatus(activePlan.status)} />
                <Meta label="搜索方式" value={`${activePlan.search_strategy === "grid" ? "确定性网格" : "固定种子随机"} / seed ${activePlan.random_seed}`} />
                <Meta label="预算" value={`${activePlan.max_trials ?? "—"} 个方案 / ${Math.ceil((activePlan.time_budget_seconds ?? 0) / 60)} 分钟`} />
                <Meta label="数据切分" value="训练集 + 验证集" />
                <div className="leading-5 text-slate-500">最终保留测试只给少量冻结候选使用，禁止参与参数搜索。</div>
              </div>
            ) : (
              <Empty>方向批准后创建计划；预算、切分、成本、目标和停止条件缺一不可。</Empty>
            )}
          </Panel>
          <Panel title="研究流程">
            <label className="mb-2 block text-xs text-slate-500" htmlFor="pipeline-profile">
              验证深度
            </label>
            <select
              id="pipeline-profile"
              value={pipelineProfileId}
              onChange={(event) => setPipelineProfileId(event.target.value)}
              className="mb-3 w-full rounded-lg border border-white/10 bg-[#071017] px-3 py-2 text-xs text-slate-200"
            >
              {(profiles.data ?? []).map((profile) => (
                <option key={profile.id} value={profile.id}>{cnProfile(profile.id)}</option>
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
                      <div className="text-slate-200">{cnStage(stage.id)}</div>
                      <div className="text-slate-500">
                        {cnStatus(result?.status ?? "not_evaluated")} · 失败即停 {stage.stop_on_fail ? "开启" : "关闭"}
                      </div>
                    </div>
                  </div>
                );
              })}
              {!selectedProfile ? <Empty>研究流程配置读取中。</Empty> : null}
            </div>
          </Panel>
          <Panel title="是否值得继续研究">
            {latestViability ? (
              <div className="space-y-2 text-xs">
                <Meta label="可行性门槛" value={cnStatus(latestViability.status)} />
                <div className="leading-5 text-slate-400">{latestViability.reasons.join("；")}</div>
              </div>
            ) : (
              <Empty>尚无可行性结果；“只比基准少亏”不会成为可用策略候选。</Empty>
            )}
            <div className="mt-3 border-t border-white/[0.06] pt-3 text-xs text-slate-400">
              策略结论：<span className="text-slate-200">{cnOutcome(latestOutcome?.outcome_type ?? "未记录")}</span>
            </div>
          </Panel>
          <Panel title="批量参数对比">
            {batchSummary.data?.trial_count ? (
              <div className="space-y-2 text-xs">
                <Meta
                  label="证据类型"
                  value={cnEvidence(batchSummary.data.evidence_mode)}
                />
                <Meta label="完成情况" value={`${batchSummary.data.succeeded_count}/${batchSummary.data.trial_count} 个方案成功`} />
                <div className="overflow-x-auto rounded-xl border border-white/10">
                  <table className="min-w-[760px] w-full border-collapse text-left text-[11px]">
                    <thead className="bg-white/[0.04] text-slate-400">
                      <tr>
                        <th className="px-2 py-2 font-medium">参数方案</th>
                        <th className="px-2 py-2 font-medium">验证收益</th>
                        <th className="px-2 py-2 font-medium">相对基准</th>
                        <th className="px-2 py-2 font-medium">PF</th>
                        <th className="px-2 py-2 font-medium">单笔期望</th>
                        <th className="px-2 py-2 font-medium">最大回撤</th>
                        <th className="px-2 py-2 font-medium">交易数</th>
                        <th className="px-2 py-2 font-medium">结论</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(trials.data ?? []).map((trial) => (
                        <tr key={trial.id} className="border-t border-white/[0.06] text-slate-300">
                          <td className="px-2 py-2 text-slate-100">{formatTrialParameters(trial.parameters)}</td>
                          <td className="px-2 py-2">{formatPercent(trial.metrics.validation_net_return)}</td>
                          <td className={`px-2 py-2 ${trialDelta(trial, trials.data ?? []) > 0 ? "text-emerald-200" : trialDelta(trial, trials.data ?? []) < 0 ? "text-rose-200" : "text-slate-400"}`}>
                            {formatSignedPercent(trialDelta(trial, trials.data ?? []))}
                          </td>
                          <td className="px-2 py-2">{formatNumber(trial.metrics.validation_profit_factor, 3)}</td>
                          <td className="px-2 py-2">{formatPercent(trial.metrics.validation_expectancy)}</td>
                          <td className="px-2 py-2">{formatPercent(trial.metrics.validation_max_drawdown_abs)}</td>
                          <td className="px-2 py-2">{formatNumber(trial.metrics.validation_trade_count, 0)}</td>
                          <td className="px-2 py-2">{trialConclusion(trial, trials.data ?? [])}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="rounded-lg bg-white/[0.03] p-2 leading-5 text-slate-400">
                  稳定区间：{JSON.stringify(batchSummary.data.stable_parameter_ranges)}
                </div>
                <div className="text-slate-500">
                  表格并排展示每个参数方案及相对基准差异。“少亏”只记为有改善，不能变成可用策略候选；孤立最高点也不会自动晋升。
                </div>
                {!batchSummary.data.research_conclusion_allowed ? (
                  <div className="rounded-lg border border-amber-300/20 bg-amber-300/10 p-2 leading-5 text-amber-100">
                    当前是测试或不可用证据，只验证批量执行连线；这些数值不能形成候选、收益或稳定性结论。
                  </div>
                ) : null}
              </div>
            ) : (
              <Empty>尚无真实批量结果。测试数据必须明确标记，不能伪装成盈利候选。</Empty>
            )}
          </Panel>
          <Panel title="亏损归因与多周期漏斗">
            {validationFunnel && validationAttribution ? (
              <div className="space-y-2 text-xs">
                <Meta
                  label="1小时趋势标记K线"
                  value={String(
                    validationFunnel.trend_1h.trend_leg_count ??
                      Object.values(
                        validationFunnel.trend_1h.bar_counts_by_direction ?? {},
                      ).reduce((total, item) => total + item, 0),
                  )}
                />
                <Meta
                  label="15分钟回调候选"
                  value={
                    validationFunnel.pullback_candidates_15m.count === null
                      ? "旧结果未记录"
                      : String(validationFunnel.pullback_candidates_15m.count)
                  }
                />
                <Meta
                  label="15分钟确认"
                  value={
                    validationFunnel.confirmations_15m === null
                      ? "旧结果未单独记录"
                      : String(validationFunnel.confirmations_15m)
                  }
                />
                <Meta
                  label="5分钟触发"
                  value={String(validationFunnel.trigger_records_5m)}
                />
                <Meta
                  label="实际成交"
                  value={String(validationFunnel.filled_entries)}
                />
                <Meta
                  label="费用 / 毛正收益"
                  value={String(
                    validationAttribution.costs
                      .fees_as_fraction_of_gross_positive_price_pnl ?? "—",
                  )}
                />
                <div className="rounded-lg bg-white/[0.03] p-2 leading-5 text-slate-400">
                  Long/Short、退出原因、持仓时间、UTC 时段/星期、止损距离、首次入场/再入和
                  连续胜负均已按 train/validation 分开。此报告复用现有 trades/signals，
                  未重新回测，不能证明因果。
                </div>
                <div className="text-amber-200">
                  多周期信号已执行（5m/15m/1h）；滚动样本外与多时间段验证尚未执行。
                </div>
              </div>
            ) : (
              <Empty>
                viability 失败后可在授权范围内复用现有 trades/signals 做廉价归因；
                不重新加载行情，不重新回测。
              </Empty>
            )}
          </Panel>
          <Panel title="失败策略中的局部改进">
            {componentHypotheses.data?.length ? (
              <div className="mb-3 space-y-2">
                {componentHypotheses.data.slice(0, 3).map((item) => (
                  <div key={item.id} className="rounded-lg border border-sky-300/15 p-2 text-xs">
                    <div className="text-slate-100">{item.title}</div>
                    <div className="mt-1 text-slate-400">
                      {cnComponentType(item.component_type)} · {item.suggested_trials} 个方案 · {cnSource(item.source)}
                    </div>
                    <div className="mt-1 text-amber-200">{cnEvidence(item.contamination_status)}</div>
                  </div>
                ))}
                <div className="text-[11px] leading-4 text-slate-500">
                  这些是确定性规则分析器生成的初查草案，不是内嵌 AI 建议；
                  批准一次诊断批次预算后才运行 3–5 个单组件方案。
                </div>
              </div>
            ) : null}
            {components.data?.length ? (
              components.data.slice(0, 3).map((component) => (
                <div key={component.id} className="mb-2 rounded-lg border border-white/10 p-2 text-xs last:mb-0">
                  <div className="text-slate-200">{component.name}</div>
                  <div className="mt-1 text-amber-200">{cnOutcome(component.status)}</div>
                  <div className="mt-1 text-slate-500">
                    {componentEvidence.data?.find((item) => item.id === component.evidence_id)
                      ?.out_of_sample_status
                      ? cnStatus(componentEvidence.data?.find((item) => item.id === component.evidence_id)?.out_of_sample_status ?? "")
                      : "证据待补充"}
                  </div>
                </div>
              ))
            ) : (
              <Empty>失败策略可做廉价归因；组件只能保留为诊断改善或组件候选，不会自动成为已验证因子。</Empty>
            )}
          </Panel>
          <Panel title="适合与不适合行情">
            {Object.keys(regimeMetrics).length ? (
              <div className="space-y-2 text-xs">
                <Meta
                  label="证据级别"
                  value={cnEvidence(
                    diagnosticReport.data?.regime_diagnostic.evidence_status ??
                      latestRegime?.evidence_status ??
                      "screening",
                  )}
                />
                <Meta
                  label="分析类型"
                  value={
                    latestRegime?.mode === "regime_validation"
                      ? "正式行情验证"
                      : "行情适配初查"
                  }
                />
                {regimeGroups.suitable.length === 0 ? (
                  <div className="rounded-lg border border-rose-300/20 bg-rose-300/10 p-2 leading-5 text-rose-100">
                    当前未发现适合行情。所有有交易样本的分组净收益均未形成正向证据，不建议继续投入完整策略调参。
                  </div>
                ) : null}
                <RegimeHeatmap metrics={regimeMetrics} groups={regimeGroups} />
                <div className="leading-5 text-amber-200">
                  标签只使用当时可观察的已收盘 1 小时 K 线，并延后一根生效。当前初查只能说明历史关联，不能作为正式适用行情声明。
                </div>
              </div>
            ) : (
              <Empty>
                可行性门槛前只允许“行情适配初查”；正式行情验证必须引用同一策略已通过的可行性结果。
              </Empty>
            )}
            <div className="mt-3 border-t border-white/[0.06] pt-3 text-xs leading-5 text-slate-400">
              Pine 来源先检查语义、重绘、多周期和少量代表交易；完整 TradingView 对账在快速初筛与可行性通过后进行。
            </div>
          </Panel>
          <Panel title="审批与成果">
            <div className="mb-3 text-xs leading-5 text-slate-400">
              基准冻结后不可覆盖；策略差异、实验、参数方案与报告将作为独立研究证据展示。
            </div>
            <button
              type="button"
              disabled={!activeDraft || Boolean(activeDraft.baseline_version_id) || freeze.isPending}
              onClick={() => freeze.mutate()}
              className="w-full rounded-xl border border-emerald-300/30 bg-emerald-300/10 px-3 py-2.5 text-sm font-medium text-emerald-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {currentBaselineId
                ? "基准版本 v0 已冻结"
                : "确认当前策略并冻结基准版本 v0"}
            </button>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

function RegimeHeatmap({
  metrics,
  groups,
}: {
  metrics: Record<string, Record<string, number>>;
  groups: Record<string, string[]>;
}) {
  const trends = [
    ["trend_down", "下跌"],
    ["trend_neutral", "震荡"],
    ["trend_up", "上涨"],
  ] as const;
  const volatilities = [
    ["low_vol", "低波动"],
    ["normal_vol", "正常波动"],
    ["high_vol", "高波动"],
  ] as const;
  return (
    <div className="overflow-x-auto rounded-xl border border-white/10">
      <div className="grid min-w-[330px] grid-cols-4 text-[10px]">
        <div className="bg-white/[0.04] p-2 text-slate-500">趋势 × 波动</div>
        {volatilities.map(([, label]) => (
          <div key={label} className="bg-white/[0.04] p-2 text-center text-slate-400">
            {label}
          </div>
        ))}
        {trends.flatMap(([trend, trendLabel]) => [
          <div key={`${trend}-label`} className="border-t border-white/[0.06] p-2 text-slate-400">
            {trendLabel}
          </div>,
          ...volatilities.map(([volatility]) => {
            const key = `${trend}__${volatility}`;
            const metric = metrics[key];
            const group = regimeGroup(key, groups);
            const tone =
              group === "suitable"
                ? "bg-emerald-300/12 text-emerald-100"
                : group === "conditional"
                  ? "bg-amber-300/12 text-amber-100"
                  : group === "blocked"
                    ? "bg-rose-300/12 text-rose-100"
                    : "bg-slate-300/[0.04] text-slate-400";
            return (
              <div
                key={key}
                className={`border-l border-t border-white/[0.06] p-2 text-center ${tone}`}
              >
                <div className="font-medium">{cnRegimeGroup(group)}</div>
                {metric ? (
                  <>
                    <div className="mt-1">{formatPercent(metric.net_return)}</div>
                    <div className="mt-0.5 text-[9px] opacity-75">
                      PF {formatNumber(metric.profit_factor, 2)} · {formatNumber(metric.trade_count, 0)} 笔
                    </div>
                  </>
                ) : (
                  <div className="mt-1 text-[9px] opacity-70">无样本</div>
                )}
              </div>
            );
          }),
        ])}
      </div>
    </div>
  );
}

function buildTopConclusion({
  hasBaseline,
  outcome,
  viability,
  suitableRegimeCount,
  regimeCount,
}: {
  hasBaseline: boolean;
  outcome: string | undefined;
  viability: string | undefined;
  suitableRegimeCount: number;
  regimeCount: number;
}) {
  if (!hasBaseline) {
    return {
      tone: "neutral" as const,
      title: "尚未形成策略结论",
      detail: "先保存并冻结原样基准，再运行有边界的初筛。",
    };
  }
  if (outcome === "rejected" || viability === "failed") {
    if (suitableRegimeCount === 0 && regimeCount > 0) {
      return {
        tone: "danger" as const,
        title: "当前未发现适合行情，不建议继续投入完整策略调参",
        detail:
          "策略未通过是否值得继续研究门槛；行情初查中的各分组也未形成正向证据。可只保留有单独证据的局部组件。",
      };
    }
    return {
      tone: "danger" as const,
      title: "当前策略未通过继续研究门槛",
      detail: "停止昂贵验证；只允许复用已有结果做廉价归因和局部组件假设。",
    };
  }
  if (viability === "passed") {
    return {
      tone: "success" as const,
      title: "当前策略通过初步可行性门槛",
      detail: "仍需成本敏感性、行情验证和独立锁定测试，不能视为可实盘策略。",
    };
  }
  return {
    tone: "neutral" as const,
    title: "策略正在等待可行性结论",
    detail: "页面只展示当前会话和当前基准的证据，不再混入其他策略结果。",
  };
}

function regimeGroup(key: string, groups: Record<string, string[]>) {
  if ((groups.suitable ?? []).includes(key)) return "suitable";
  if ((groups.conditional ?? []).includes(key)) return "conditional";
  if ((groups.blocked ?? []).includes(key)) return "blocked";
  return "unknown";
}

function cnRegimeGroup(group: string) {
  return {
    suitable: "适合",
    conditional: "有条件",
    blocked: "不适合",
    unknown: "证据不足",
  }[group] ?? "证据不足";
}

function shortId(value: string) {
  const [prefix, suffix] = value.split("_", 2);
  if (!suffix) return value.length > 14 ? `${value.slice(0, 12)}…` : value;
  return `${prefix}_${suffix.slice(0, 8)}…`;
}

function cnResearchMode(value: string) {
  return { quick: "快捷模式", guided: "引导模式", expert: "专家模式" }[value] ?? value;
}

function cnStage(value: string) {
  return {
    correctness: "规则与数据检查",
    smoke: "小范围试跑",
    fast_screen: "快速初筛",
    viability: "是否值得继续研究",
    loss_attribution: "亏损原因分析",
    regime_diagnostic: "行情适配初查",
    component_hypothesis_generation: "局部改进方向",
    cheap_cost_sensitivity: "廉价成本敏感性",
    full_validation: "完整验证",
    locked_test: "最终保留测试",
    dry_run: "模拟运行",
  }[value] ?? value;
}

function cnProfile(value: string) {
  return {
    smoke: "小范围试跑",
    fast_screen: "快速初筛",
    full_validation: "完整验证",
  }[value] ?? value;
}

function cnStatus(value: string) {
  return {
    inbox: "待整理",
    formalized: "已形式化",
    baseline: "基准版本",
    candidate: "候选版本",
    validated: "已验证",
    dry_run: "模拟运行",
    degraded: "已退化",
    retired: "已停用",
    draft: "草稿",
    awaiting_confirmation: "等待确认",
    baseline_frozen: "基准已冻结",
    queued: "排队中",
    running: "运行中",
    succeeded: "已完成",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    paused: "已暂停",
    waiting_approval: "等待批准",
    waiting_user_approval: "等待用户批准",
    waiting_required_input: "等待必要输入",
    completed_scope: "本次范围已完成",
    gate_failed: "研究门槛未通过",
    budget_exhausted: "预算已用完",
    blocked_dependency: "依赖不可用",
    safety_refusal: "安全门禁已拒绝",
    active: "生效中",
    approved: "已批准",
    executing: "执行中",
    evaluated: "已评估",
    accepted: "已接受",
    rejected: "未通过",
    expired: "已过期",
    passed: "通过",
    blocked: "被门禁阻止",
    not_evaluated: "尚未评估",
    not_recorded: "尚未记录",
    not_queued: "尚未排队",
    "not queued": "尚未排队",
    awaiting_heartbeat: "等待本地 Agent 活动",
    screening: "初步分析",
    insufficient_history: "历史不足",
    extended_validation: "扩展验证",
    not_tested: "尚未测试",
  }[value] ?? value;
}

function cnOutcome(value: string) {
  return {
    diagnostic_improvement: "有改善，但还不能使用",
    component_candidate: "局部组件候选",
    strategy_candidate: "策略候选",
    validated: "已验证",
    rejected: "未通过，停止继续投入",
    未记录: "未记录",
  }[value] ?? cnStatus(value);
}

function cnSource(value: string) {
  return {
    natural_language: "自然语言",
    pine: "Pine Script",
    file: "文件",
    external_agent: "本地 AI",
    manual: "人工",
    deterministic_rule_analyzer: "规则分析器",
  }[value] ?? value;
}

function cnComponentType(value: string) {
  return {
    entry: "入场组件",
    filter: "过滤组件",
    exit: "出场组件",
    risk: "风控组件",
    execution: "执行组件",
  }[value] ?? value;
}

function cnEvidence(value: string) {
  return {
    research: "真实研究证据",
    fixture: "测试连线证据",
    unavailable: "证据不可用",
    screening: "初步分析",
    screening_contaminated: "已观察验证集的初查证据",
    insufficient_history: "历史不足",
    extended_validation: "扩展验证",
    diagnostic: "诊断证据",
  }[value] ?? value;
}

function cnEvent(value: string) {
  const known: Record<string, string> = {
    "research_session.created": "创建研究会话",
    "research_session.mode_updated": "更新研究模式",
    "message.created": "保存用户消息",
    "strategy_draft.created": "保存策略草稿",
    "strategy_baseline.frozen": "冻结基准版本",
    "experiment_plan.created": "创建实验计划",
    "experiment_plan.approved": "批准实验计划",
    "agent_run.created": "创建 AI 研究任务",
    "research_handoff.recorded": "记录停止原因与下一步",
  };
  return known[value] ?? value.replaceAll("_", " ").replaceAll(".", " · ");
}

function cnActor(value: string) {
  return {
    user: "用户",
    system: "系统",
    external_agent: "本地 AI",
    embedded_agent: "网页 AI",
    worker: "确定性执行程序",
  }[value] ?? value;
}

function formatTrialParameters(parameters: Record<string, unknown>) {
  return Object.entries(parameters)
    .map(([key, value]) => {
      const cnKey =
        {
          structure_exit_variant: "结构退出",
          entry_mode: "入场方式",
        }[key] ?? key;
      const cnValue =
        {
          current: "当前基准",
          tighter: "更快确认",
          looser: "更慢确认",
          confirmation_candle_breakout: "确认K线突破",
        }[String(value)] ?? String(value);
      return `${cnKey}：${cnValue}`;
    })
    .join("；");
}

function trialDelta(trial: Trial, allTrials: Trial[]) {
  if (Number.isFinite(trial.metrics.incremental_net_return)) {
    return trial.metrics.incremental_net_return;
  }
  if (Number.isFinite(trial.metrics.validation_net_return_delta_vs_baseline)) {
    return trial.metrics.validation_net_return_delta_vs_baseline;
  }
  const baselineTrial = allTrials.find(
    (item) =>
      item.metrics.incremental_net_return === 0 ||
      Object.values(item.parameters).some((value) =>
        ["current", "baseline"].includes(String(value)),
      ),
  );
  const value = trial.metrics.validation_net_return;
  const baselineValue = baselineTrial?.metrics.validation_net_return;
  return typeof value === "number" &&
    Number.isFinite(value) &&
    typeof baselineValue === "number" &&
    Number.isFinite(baselineValue)
    ? value - baselineValue
    : Number.NaN;
}

function trialConclusion(trial: Trial, allTrials: Trial[]) {
  if (trial.status !== "succeeded") return "运行失败";
  const net = trial.metrics.validation_net_return;
  const profitFactor = trial.metrics.validation_profit_factor;
  const expectancy = trial.metrics.validation_expectancy;
  if (net >= 0 && profitFactor >= 1 && expectancy > 0) return "可进入下一步验证";
  const delta = trialDelta(trial, allTrials);
  if (delta > 0) return "有改善，但还不能使用";
  if (delta < 0) return "比基准更差";
  return "当前基准";
}

function formatPercent(value: number | undefined) {
  return Number.isFinite(value) ? `${((value ?? 0) * 100).toFixed(2)}%` : "—";
}

function formatSignedPercent(value: number) {
  if (!Number.isFinite(value)) return "—";
  const percent = value * 100;
  return `${percent > 0 ? "+" : ""}${percent.toFixed(2)}%`;
}

function formatNumber(value: number | undefined, digits: number) {
  return Number.isFinite(value) ? (value ?? 0).toFixed(digits) : "—";
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
