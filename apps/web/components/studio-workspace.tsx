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
  EngineReconciliationStatus,
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
import { BatchProgressPanel } from "@/components/studio/batch-progress";
import { ComponentLibrary } from "@/components/studio/component-library";
import { IntakeComposer } from "@/components/studio/intake-composer";
import { ImprovementDirections } from "@/components/studio/improvement-directions";
import { RegimeEvidence } from "@/components/studio/regime-evidence";
import { ResearchCharts } from "@/components/studio/research-charts";
import {
  buildTopConclusion,
  StudioOverview,
} from "@/components/studio/studio-overview";
import {
  cnActor,
  cnEvent,
  cnEvidence,
  cnOutcome,
  cnProfile,
  cnResearchMode,
  cnSource,
  cnStage,
  cnStatus,
  formatNumber,
  formatPercent,
  formatSignedPercent,
  formatTrialParameters,
  trialConclusion,
  trialDelta,
} from "@/components/studio/studio-labels";
import {
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

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
    "可输入下一份策略或新研究假设。当前已保留一份未通过策略及诊断性组件证据；网页模型未配置。",
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
  const engineReconciliation = useQuery({
    queryKey: ["engine-reconciliation", currentSubjectId],
    queryFn: () =>
      apiFetch<EngineReconciliationStatus>(
        `/api/engine-reconciliation/status?subject_id=${encodeURIComponent(currentSubjectId ?? "")}&market_profile=crypto_perpetual.binance.eth`,
      ),
    enabled: Boolean(currentSubjectId),
    retry: false,
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
  const allComponents = useQuery({
    queryKey: ["component-candidates-all", currentBaselineId],
    queryFn: () =>
      apiFetch<ComponentCandidate[]>(
        `/api/component-candidates?include_archived=true&source_strategy_version_id=${encodeURIComponent(currentBaselineId ?? "")}`,
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
        `已切换为${researchModes.data?.find((item) => item.mode === updated.research_mode)?.label ?? updated.research_mode}。网页、本地研究助手和命令行将读取同一会话配置。`,
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
        "已一次授权规则检查 → 小范围试跑 → 快速初筛 → 可行性门槛；内部阶段不再逐次询问，门槛失败或授权范围结束时才停止。",
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
      setNotice("已批准明确的改进方向并创建不可变候选快照；冻结基准未被覆盖。");
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
      setNotice("改进方向已提交等待审批；批准按钮会自动携带精确研究对象。");
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
      setNotice("预算已按明确研究对象调整；如原来等待审批，现已退回草稿供重新审阅。");
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
      setNotice("已记录当前后台任务的取消请求；现有参数试验证据保留，执行程序会在安全批次边界停止。");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });
  const archiveComponent = useMutation({
    mutationFn: (candidateId: string) =>
      apiFetch(`/api/component-candidates/${candidateId}/archive`, {
        method: "POST",
        body: JSON.stringify({
          subject_id: candidateId,
          confirmed_by_user: true,
          reason: "用户从活跃研究组件列表归档",
        }),
      }),
    onSuccess: () => {
      setNotice("组件已从活跃列表软归档；原始证据、失败记录和审计事件均保留。");
      queryClient.invalidateQueries({ queryKey: ["component-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["component-candidates-all"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });
  const restoreComponent = useMutation({
    mutationFn: (candidateId: string) =>
      apiFetch(`/api/component-candidates/${candidateId}/restore`, {
        method: "POST",
        body: JSON.stringify({
          subject_id: candidateId,
          confirmed_by_user: true,
          reason: "用户恢复策略组件",
        }),
      }),
    onSuccess: () => {
      setNotice("组件已恢复到活跃列表；原有证据和审计链保持不变。");
      queryClient.invalidateQueries({ queryKey: ["component-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["component-candidates-all"] });
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
        "原始来源已保存为策略草稿。未运行模型形式化；请先审阅原文和歧义，再人工冻结基准。",
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
      setNotice("冻结基准 v0 已保存且不可覆盖。后续修改必须创建独立改进方案和新版本。");
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
      archiveComponent.error ??
      restoreComponent.error ??
      updateResearchMode.error;
    if (error instanceof z.ZodError) return error.issues[0]?.message;
    return error instanceof Error ? error.message : null;
  }, [
    approveDirection.error,
    archiveComponent.error,
    authorizeToViability.error,
    cancelBatchJob.error,
    freeze.error,
    reviseDirectionBudget.error,
    restoreComponent.error,
    saveIntake.error,
    submitDirection.error,
    updateResearchMode.error,
  ]);

  return (
    <div className="min-h-screen p-4 md:p-6">
      <StudioOverview
        strategyTitle={activeSession?.title}
        researchMode={cnResearchMode(activeSession?.research_mode ?? "guided")}
        providerConfigured={Boolean(project.data?.ai_provider.configured)}
        externalAgentStatus={
          agent.data?.external_agent.connection_status ?? "awaiting_heartbeat"
        }
        conclusion={topConclusion}
        handoff={handoff.data}
        versioningAvailable={Boolean(versioning.data?.available)}
      />

      <div className="mt-5 grid min-w-0 grid-cols-[minmax(0,1fr)] gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <aside className="order-3 min-w-0 space-y-4 xl:order-2 xl:col-start-2 xl:row-span-2">
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
                className="min-w-0 w-full rounded-lg border border-white/10 bg-[#071017] px-2 py-2 text-xs text-slate-200"
              >
                {(sessions.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
              <Meta label="状态" value={cnStatus(activeSession?.status ?? "inbox")} />
              <Meta label="策略草稿" value={activeDraft ? "1 份" : "0 份"} />
              <Meta label="当前基准" value={currentBaselineId ? "已冻结" : "未冻结"} />
              {currentBaselineId ? (
                <TechnicalDetails label="查看基准技术 ID">
                  <TechnicalId label="冻结基准" value={currentBaselineId} />
                </TechnicalDetails>
              ) : null}
            </div>
          </Panel>
          <Panel title="研究模式">
            <div className="space-y-2">
              <label htmlFor="research-mode" className="text-xs text-slate-500">
                运行方式
              </label>
              <select
                id="research-mode"
                value={activeSession?.research_mode ?? "guided"}
                onChange={(event) =>
                  updateResearchMode.mutate(
                    event.target.value as "quick" | "guided" | "expert",
                  )
                }
                disabled={!activeSession || updateResearchMode.isPending}
                className="w-full rounded-xl border border-white/10 bg-[#071017] px-3 py-2.5 text-sm text-slate-200"
              >
                {(researchModes.data ?? []).map((item) => (
                  <option key={item.mode} value={item.mode}>
                    {item.label}
                  </option>
                ))}
              </select>
              <div className="rounded-lg bg-white/[0.03] p-3 text-xs leading-5 text-slate-400">
                {researchModes.data?.find(
                  (item) => item.mode === activeSession?.research_mode,
                )?.description ?? "引导模式每轮只在关键决策处询问一次。"}
              </div>
              <div className="rounded-lg border border-dashed border-white/15 p-3 text-[11px] leading-5 text-slate-500">
                本地研究助手无需另选模式：开始研究任务时自动读取当前会话的
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
                  一次授权安全范围后，规则检查、小范围试跑、快速初筛与读取已保存指标的
                  可行性评估不再逐阶段询问。门槛失败时自动停止。
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
                  授权“研究到可行性结论”
                </button>
                <div className="text-[11px] leading-4 text-slate-500">
                  页面自动携带当前研究对象；无需复制长 ID。未通过策略只能做已授权廉价诊断。
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
                默认预算：{budgetPolicy.data?.max_hypotheses ?? "—"} 个改进假设 / {budgetPolicy.data?.max_trials_total ?? "—"} 个参数方案；创建会话后显示已用与剩余。
              </Empty>
            )}
          </Panel>
          <Panel title="批量参数进度">
            <BatchProgressPanel
              summary={batchSummary.data}
              job={activeBatchJob}
              onCancel={(jobId) => cancelBatchJob.mutate(jobId)}
            />
          </Panel>
        </aside>

        <IntakeComposer
          title={title}
          sourceType={sourceType}
          content={content}
          activeDraft={activeDraft}
          notice={notice}
          pending={saveIntake.isPending}
          error={formError}
          onTitleChange={setTitle}
          onSourceTypeChange={setSourceType}
          onContentChange={setContent}
          onSubmit={() => saveIntake.mutate()}
        />

        <aside className="order-1 flex min-w-0 flex-col gap-4 rounded-2xl border border-white/10 bg-[#0a151e]/70 p-4 md:p-6 xl:col-start-1 xl:order-1">
          <Panel title="结构化策略">
            <div className="space-y-2 text-sm">
              <Meta label="策略草稿" value={activeDraft ? "已创建" : "未创建"} />
              <Meta label="来源" value={cnSource(activeDraft?.source_type ?? "—")} />
              <Meta label="状态" value={cnStatus(activeDraft?.status ?? "—")} />
              <Meta label="模型形式化" value="未运行" />
              {activeDraft ? (
                <TechnicalDetails>
                  <TechnicalId label="策略草稿" value={activeDraft.id} />
                </TechnicalDetails>
              ) : null}
            </div>
          </Panel>
          <Panel title="改进方向（最多 3 个）">
            <ImprovementDirections
              directions={directions.data ?? []}
              providerConfigured={Boolean(project.data?.ai_provider.configured)}
              pending={
                submitDirection.isPending ||
                approveDirection.isPending ||
                reviseDirectionBudget.isPending
              }
              onSubmit={(proposalId) => submitDirection.mutate(proposalId)}
              onApprove={(proposalId) => approveDirection.mutate(proposalId)}
              onBudgetChange={(proposalId, trials, minutes) =>
                reviseDirectionBudget.mutate({
                  proposalId,
                  trials,
                  minutes,
                })
              }
            />
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
          <Panel title="第二引擎逐笔对账">
            <div className="space-y-2 text-xs">
              <Meta
                label="当前状态"
                value={
                  engineReconciliation.data?.eligible
                    ? "门槛通过，等待外部引擎接入"
                    : "尚未满足进入条件"
                }
              />
              <div className="leading-5 text-slate-400">
                {engineReconciliation.data?.reason ??
                  "只有同一策略、同一市场通过可行性门槛后，才能准备 Freqtrade 第二引擎逐笔对账。"}
              </div>
              <div className="rounded-lg border border-dashed border-white/15 p-2 leading-5 text-slate-500">
                Freqtrade 始终是客户自行安装的外部进程。当前没有真实 IStrategy 转换器，因此不会创建虚假的对账任务，也不会启用 Hyperopt、FreqAI 或实盘。
              </div>
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
                        <th className="px-2 py-2 font-medium">盈亏效率</th>
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
                <details className="rounded-lg bg-white/[0.03] p-2 leading-5 text-slate-400">
                  <summary className="cursor-pointer text-slate-300">查看稳定参数区间</summary>
                  <div className="mt-2 break-words">
                    {Object.entries(batchSummary.data.stable_parameter_ranges)
                      .map(([key, value]) => `${key}：${JSON.stringify(value)}`)
                      .join("；") || "尚未形成连续稳定区间"}
                  </div>
                </details>
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
            <div className="mt-5 border-t border-white/[0.06] pt-5">
              <ResearchCharts
                bundleId={runBundles.data?.[0]?.bundle_id ?? null}
                trials={trials.data ?? []}
              />
            </div>
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
            <ComponentLibrary
              hypotheses={componentHypotheses.data ?? []}
              candidates={components.data ?? []}
              archivedCandidates={(allComponents.data ?? []).filter(
                (item) => item.archived_at !== null,
              )}
              evidence={componentEvidence.data ?? []}
              onArchive={(candidateId) => archiveComponent.mutate(candidateId)}
              onRestore={(candidateId) => restoreComponent.mutate(candidateId)}
            />
          </Panel>
          <Panel title="适合与不适合行情">
            <RegimeEvidence
              metrics={regimeMetrics}
              evidenceStatus={
                diagnosticReport.data?.regime_diagnostic.evidence_status ??
                latestRegime?.evidence_status ??
                "screening"
              }
              formalValidation={latestRegime?.mode === "regime_validation"}
            />
            <div className="mt-3 border-t border-white/[0.06] pt-3 text-xs leading-5 text-slate-400">
              Pine 来源先检查语义、重绘、多周期和少量代表交易；完整 TradingView 对账在快速初筛与可行性通过后进行。
            </div>
          </Panel>
          <Panel title="研究助手总结与技术记录">
            <div className="text-sm leading-6 text-slate-300">
              {latestViability?.status === "failed"
                ? "规则分析器结论：完整策略未通过可行性门槛。系统已停止昂贵阶段，只保留最多三个不同类别的局部诊断方向。"
                : latestViability?.status === "passed"
                  ? "确定性结论：策略已通过初步门槛，但第二引擎、完整压力测试和最终保留测试尚未完成。"
                  : "当前尚无可行性结论。网页模型未配置时，这里只总结真实状态，不生成虚构建议。"}
            </div>
            <details className="mt-4 rounded-lg border border-white/[0.08] p-3 text-xs text-slate-500">
              <summary className="cursor-pointer text-slate-400">
                展开研究结果包与追加式时间线
              </summary>
              <div className="mt-3 space-y-4">
                <div>
                  <div className="mb-2 text-slate-300">研究结果包</div>
                  {(runBundles.data ?? []).slice(0, 4).map((bundle) => (
                    <div key={bundle.bundle_id} className="mb-2 border-l border-sky-300/20 pl-3">
                      {cnStatus(bundle.status)} · {bundle.report_type}
                      <div className="break-all text-slate-600">
                        {bundle.report_artifact_key}
                      </div>
                    </div>
                  ))}
                  {!runBundles.data?.length ? <div>尚无研究结果包。</div> : null}
                </div>
                <div>
                  <div className="mb-2 text-slate-300">追加式时间线</div>
                  {scopedEvents.map((event) => (
                    <div key={event.id} className="mb-2 border-l border-emerald-300/20 pl-3">
                      {cnEvent(event.event_type)} · {cnActor(event.actor_type)}
                    </div>
                  ))}
                  {!scopedEvents.length ? <div>尚无当前会话事件。</div> : null}
                </div>
              </div>
            </details>
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

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-white/15 p-3 text-xs leading-5 text-slate-500">
      {children}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  const order =
    {
      "结构化策略": "order-[0]",
      "是否值得继续研究": "order-[1]",
      "亏损归因与多周期漏斗": "order-[2]",
      "改进方向（最多 3 个）": "order-[3]",
      "实验计划": "order-[4]",
      "批量参数对比": "order-[5]",
      "失败策略中的局部改进": "order-[6]",
      "适合与不适合行情": "order-[7]",
      "第二引擎逐笔对账": "order-[8]",
      "研究流程": "order-[9]",
      "研究助手总结与技术记录": "order-[10]",
      "审批与成果": "order-[11]",
    }[title] ?? "";
  return (
    <section className={`min-w-0 rounded-2xl border border-white/10 bg-[#0a151e]/90 p-4 ${order}`}>
      <h2 className="mb-3 text-sm font-semibold tracking-tight text-slate-300">{title}</h2>
      {children}
    </section>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-white/[0.06] pb-2 last:border-0 last:pb-0">
      <span className="text-slate-500">{label}</span>
      <span className="max-w-[65%] text-right text-slate-200">{value}</span>
    </div>
  );
}
