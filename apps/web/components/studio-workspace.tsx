"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { z } from "zod";

import {
  AgentRun,
  AgentStatus,
  AssistantEntryMode,
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
  CandidateValidationSummary,
  Job,
  PipelineProfile,
  ProjectStatus,
  ProviderStatus,
  RegimeValidation,
  ResearchBudget,
  ResearchBudgetPolicy,
  ResearchAuthorization,
  ResearchAuthorizationStage,
  ResearchDiagnosticReport,
  ResearchHandoff,
  RunBundle,
  Session,
  SessionAgentOccupancy,
  SessionDetail,
  ResearchModeDefinition,
  StrategyOutcome,
  StrategyDraft,
  StrategyVersion,
  VersioningPolicy,
} from "@/lib/api";
import { BatchProgressPanel } from "@/components/studio/batch-progress";
import { CandidateValidationResultCard } from "@/components/studio/candidate-validation-result";
import { ComponentLibrary } from "@/components/studio/component-library";
import { IntakeComposer } from "@/components/studio/intake-composer";
import { ImprovementDirections } from "@/components/studio/improvement-directions";
import { StrategyVersionLineage } from "@/components/studio/strategy-version-lineage";
import {
  ResearchAnalysis,
  ResearchAnalysisView,
} from "@/components/studio/research-analysis";
import { ResearchDecisionSheet } from "@/components/studio/research-decision-sheet";
import {
  StageContainer,
  StageTabs,
  WorkflowNavigation,
  WorkflowStage,
  WorkflowStageItem,
} from "@/components/studio/research-workflow";
import {
  buildTopConclusion,
  ResearchHandoffPanel,
  StudioOverview,
} from "@/components/studio/studio-overview";
import {
  cnActor,
  cnEvent,
  cnGateReason,
  cnOutcome,
  cnProfile,
  cnResearchMode,
  cnSource,
  cnStage,
  cnStatus,
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

type DiagnosisView =
  | ResearchAnalysisView
  | "attribution"
  | "improvements";

type ValidationView = "robustness" | "reconciliation" | "tradingview";

const WORKFLOW_STAGE_IDS: WorkflowStage[] = [
  "overview",
  "strategy",
  "screening",
  "diagnosis",
  "validation",
  "conclusion",
];

const DIAGNOSIS_VIEW_IDS: DiagnosisView[] = [
  "performance",
  "parameters",
  "regimes",
  "attribution",
  "improvements",
];

const VALIDATION_VIEW_IDS: ValidationView[] = [
  "robustness",
  "reconciliation",
  "tradingview",
];

export function StudioWorkspace() {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("ETH 永续策略研究");
  const [sourceType, setSourceType] = useState<"natural_language" | "pine" | "file">(
    "natural_language",
  );
  const [content, setContent] = useState("");
  const [assistantModeOverride, setAssistantModeOverride] =
    useState<AssistantEntryMode | null>(null);
  const [providerName, setProviderName] = useState("OpenAI");
  const [providerBaseUrl, setProviderBaseUrl] = useState(
    "https://api.openai.com/v1",
  );
  const [providerModel, setProviderModel] = useState("");
  const [providerApiKey, setProviderApiKey] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [draft, setDraft] = useState<StrategyDraft | null>(null);
  const [baseline, setBaseline] = useState<BaselineVersion | null>(null);
  const [pipelineProfileId, setPipelineProfileId] = useState("fast_screen");
  const [activeStage, setActiveStage] =
    useState<WorkflowStage>("overview");
  const [diagnosisView, setDiagnosisView] =
    useState<DiagnosisView>("performance");
  const [validationView, setValidationView] =
    useState<ValidationView>("robustness");
  const [workflowLocationInitialized, setWorkflowLocationInitialized] =
    useState(false);
  const [controlPanelOpen, setControlPanelOpen] = useState(false);
  const [creatingNewSession, setCreatingNewSession] = useState(false);
  const [researchModeEditing, setResearchModeEditing] = useState(false);
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
    refetchInterval: 5000,
  });
  const providerStatus = useQuery({
    queryKey: ["provider-status"],
    queryFn: () => apiFetch<ProviderStatus>("/api/agent/provider"),
  });
  const events = useQuery({
    queryKey: ["audit-events"],
    queryFn: () => apiFetch<AuditEvent[]>("/api/audit/events?limit=20"),
  });
  const sessions = useQuery({
    queryKey: ["research-sessions"],
    queryFn: () => apiFetch<Session[]>("/api/research/sessions"),
  });
  const activeSession = creatingNewSession
    ? null
    : session ?? sessions.data?.[0] ?? null;
  const assistantMode =
    assistantModeOverride ??
    activeSession?.assistant_entry_mode ??
    "external_agent_direct";
  const sessionDetail = useQuery({
    queryKey: ["research-session-detail", activeSession?.id],
    queryFn: () =>
      apiFetch<SessionDetail>(`/api/research/sessions/${activeSession?.id}`),
    enabled: Boolean(activeSession?.id),
    refetchInterval: 5000,
  });
  const agentOccupancy = useQuery({
    queryKey: ["session-agent-occupancy", activeSession?.id],
    queryFn: () =>
      apiFetch<SessionAgentOccupancy>(
        `/api/research/sessions/${activeSession?.id}/agent-occupancy`,
      ),
    enabled: Boolean(activeSession?.id),
    refetchInterval: 5000,
    retry: false,
  });
  const agentRuns = useQuery({
    queryKey: ["session-agent-runs", activeSession?.id],
    queryFn: () =>
      apiFetch<AgentRun[]>(
        `/api/research/sessions/${activeSession?.id}/agent-runs`,
      ),
    enabled: Boolean(activeSession?.id),
    refetchInterval: 2000,
    retry: false,
  });
  const persistedDraft = draft
    ? sessionDetail.data?.drafts.find((item) => item.id === draft.id) ?? null
    : sessionDetail.data?.drafts[0] ?? null;
  const activeDraft =
    draft?.session_id === activeSession?.id
      ? persistedDraft ?? draft
      : persistedDraft;
  const latestAgentRun =
    agentRuns.data?.find(
      (run) =>
        run.task_type === "strategy_formalization" &&
        (!activeDraft || run.subject_id === activeDraft.id),
    ) ?? null;
  const currentBaselineId =
    baseline && activeDraft?.baseline_version_id === baseline.id
      ? baseline.id
      : activeDraft?.baseline_version_id ?? null;
  const draftIsFormalized =
    Boolean(activeDraft?.baseline_version_id) ||
    activeDraft?.status === "baseline_frozen" ||
    (activeDraft?.status === "awaiting_confirmation" &&
      Object.keys(activeDraft.structured_content ?? {}).length > 0);
  const defaultFormalizationText = useMemo(() => {
    if (!activeDraft) return "";
    const structured = activeDraft.structured_content ?? {};
    return Object.keys(structured).length
      ? JSON.stringify(structured, null, 2)
      : "";
  }, [activeDraft]);
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
  const strategyVersions = useQuery({
    queryKey: ["strategy-versions", activeDraft?.id],
    queryFn: () =>
      apiFetch<StrategyVersion[]>(
        `/api/strategy-versions?strategy_id=${encodeURIComponent(activeDraft?.id ?? "")}`,
      ),
    enabled: Boolean(activeDraft?.id && currentBaselineId),
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
  const activePipelineJob = jobs.data?.find(
    (job) =>
      job.job_type === "pipeline_execution" &&
      job.payload.subject_id === currentSubjectId,
  );
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
  const candidateValidationJob = jobs.data?.find(
    (job) =>
      job.job_type === "stress_test" &&
      job.payload.intent === "generic_strategy_dsl_candidate_validation" &&
      job.payload.experiment_plan_id === activePlan?.id,
  );
  const lockedTestJob = jobs.data?.find(
    (job) =>
      job.job_type === "stress_test" &&
      job.payload.intent === "generic_strategy_dsl_locked_test" &&
      job.payload.experiment_plan_id === activePlan?.id,
  );
  const batchIsRunning = Boolean(
    activeBatchJob && ["queued", "running"].includes(activeBatchJob.status),
  );
  const candidateValidationIsRunning = Boolean(
    (candidateValidationJob &&
      ["queued", "running"].includes(candidateValidationJob.status)) ||
      (lockedTestJob &&
        ["queued", "running"].includes(lockedTestJob.status)),
  );
  const sessionHasActiveJob = Boolean(
    activeSession &&
      jobs.data?.some(
        (job) =>
          ["queued", "running"].includes(job.status) &&
          (job.payload.session_id === activeSession.id ||
            job.id === activePipelineJob?.id ||
            job.id === activeBatchJob?.id ||
            job.id === candidateValidationJob?.id),
      ),
  );
  const researchModeLocked = Boolean(
    agentOccupancy.data?.occupied || sessionHasActiveJob,
  );
  const executionStatus = agentOccupancy.data?.occupied
    ? "AI 正在处理"
    : sessionHasActiveJob
      ? "后台任务运行中"
      : "当前空闲";
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
  const candidateValidation = useQuery({
    queryKey: ["candidate-validation-summary", activePlan?.id],
    queryFn: () =>
      apiFetch<CandidateValidationSummary>(
        `/api/experiment-plans/${activePlan?.id}/candidate-validation-summary`,
      ),
    enabled: Boolean(activePlan?.id),
    refetchInterval: candidateValidationIsRunning ? 3000 : false,
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
  const baseTopConclusion = buildTopConclusion({
    hasBaseline: Boolean(currentBaselineId),
    outcome: latestOutcome?.outcome_type,
    viability: latestViability?.status,
    suitableRegimeCount: regimeGroups.suitable.length,
    regimeCount: Object.keys(regimeMetrics).length,
  });
  const topConclusion =
    candidateValidation.data?.locked_test_decision === "passed"
      ? {
          tone: "success" as const,
          title: "最终保留测试通过，等待人工审阅",
          detail:
            "冻结候选和冻结参数已在独立保留区间完成一次测试；仍未自动晋升、模拟运行或实盘。",
        }
      : candidateValidation.data?.locked_test_decision === "failed"
        ? {
            tone: "danger" as const,
            title: "最终保留测试未通过",
            detail:
              "当前候选停止。不得针对已查看的保留区间继续调参；如需继续，应建立新假设和新的保留期。",
          }
        : candidateValidation.data?.decision_status ===
    "ready_for_locked_test_review"
      ? {
          tone: "success" as const,
          title: "候选已通过有界稳健性检查",
          detail:
            "成本、滚动窗口、参数扰动和行情拆分达到当前门槛；最终保留测试仍需单独审阅和批准。",
        }
      : candidateValidation.data?.decision_status === "needs_revision"
        ? {
            tone: "danger" as const,
            title: "候选验证未通过",
            detail:
              "系统已指出最弱证据，不会自动扩大搜索、查看最终保留测试或覆盖冻结基准。",
          }
        : baseTopConclusion;
  const researchMode = activeSession?.research_mode ?? "guided";
  const quickMode = researchMode === "quick";
  const expertMode = researchMode === "expert";
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
  const inferredStage: WorkflowStage = candidateValidation.data?.decision_status
    ? "conclusion"
    : !currentBaselineId
      ? "strategy"
      : !latestViability
        ? "screening"
        : latestViability.status === "failed"
          ? "diagnosis"
          : latestViability.status === "passed"
            ? "validation"
            : "screening";
  const visibleStage = quickMode ? inferredStage : activeStage;
  const workflowStages: WorkflowStageItem[] = [
    {
      id: "strategy",
      label: "策略与基准",
      description: "输入、结构化、冻结",
      status: currentBaselineId
        ? "completed"
        : activeDraft
          ? "active"
          : "pending",
      statusLabel: currentBaselineId
        ? "已冻结"
        : activeDraft
          ? "待确认"
          : "未开始",
    },
    {
      id: "screening",
      label: "快速初筛",
      description: "规则、试跑、可行性",
      status: !currentBaselineId
        ? "locked"
        : latestViability?.status === "failed"
          ? "stopped"
          : latestViability?.status === "passed"
            ? "completed"
            : "active",
      statusLabel: !currentBaselineId
        ? "待基准"
        : latestViability
          ? cnStatus(latestViability.status)
          : "待评估",
    },
    {
      id: "diagnosis",
      label: "诊断与优化",
      description: "证据、归因、改进",
      status: !latestViability
        ? "locked"
        : latestViability.status === "failed"
          ? "active"
          : "completed",
      statusLabel: !latestViability
        ? "待初筛"
        : latestViability.status === "failed"
          ? "当前阶段"
          : "已完成",
    },
    {
      id: "validation",
      label: "深度验证",
      description: "跨期、压力、对账",
      status:
        latestViability?.status === "passed" ? "active" : "locked",
      statusLabel:
        latestViability?.status === "passed" ? "可进入" : "门槛未通过",
    },
    {
      id: "conclusion",
      label: "结论与归档",
      description: "总结、证据、成果",
      status: latestOutcome ? "completed" : "pending",
      statusLabel: latestOutcome ? "已有结论" : "待形成",
    },
  ];

  useEffect(() => {
    if (workflowLocationInitialized) return;
    if (typeof window === "undefined") return;
    const workflowDataReady =
      creatingNewSession ||
      (!activeSession && sessions.isSuccess) ||
      (Boolean(activeSession?.id) &&
        sessionDetail.isSuccess &&
        sessionDetail.data.session.id === activeSession?.id &&
        (!currentBaselineId || gates.isSuccess));
    if (!workflowDataReady) return;
    const params = new URLSearchParams(window.location.search);
    const requestedStage = params.get("stage") as WorkflowStage | null;
    const requestedView = params.get("view");
    let nextStage: WorkflowStage;
    if (
      requestedStage &&
      WORKFLOW_STAGE_IDS.includes(requestedStage)
    ) {
      nextStage = requestedStage;
    } else {
      nextStage = inferredStage;
    }
    const nextDiagnosisView =
      requestedStage === "diagnosis" &&
      requestedView &&
      DIAGNOSIS_VIEW_IDS.includes(requestedView as DiagnosisView)
        ? (requestedView as DiagnosisView)
        : null;
    const nextValidationView =
      requestedStage === "validation" &&
      requestedView &&
      VALIDATION_VIEW_IDS.includes(requestedView as ValidationView)
        ? (requestedView as ValidationView)
        : null;
    let cancelled = false;
    window.queueMicrotask(() => {
      if (cancelled) return;
      setActiveStage(nextStage);
      if (nextDiagnosisView) setDiagnosisView(nextDiagnosisView);
      if (nextValidationView) setValidationView(nextValidationView);
      setWorkflowLocationInitialized(true);
    });
    return () => {
      cancelled = true;
    };
  }, [
    activeSession,
    creatingNewSession,
    inferredStage,
    currentBaselineId,
    gates.isSuccess,
    sessionDetail.data,
    sessionDetail.isSuccess,
    sessions.isSuccess,
    workflowLocationInitialized,
  ]);

  const updateWorkflowLocation = (
    stage: WorkflowStage,
    view?: DiagnosisView | ValidationView,
  ) => {
    setActiveStage(stage);
    if (stage === "diagnosis" && view) {
      setDiagnosisView(view as DiagnosisView);
    }
    if (stage === "validation" && view) {
      setValidationView(view as ValidationView);
    }
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    url.searchParams.set("stage", stage);
    if (
      (stage === "diagnosis" || stage === "validation") &&
      view
    ) {
      url.searchParams.set("view", view);
    } else {
      url.searchParams.delete("view");
    }
    window.history.replaceState(null, "", url);
  };

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
      setResearchModeEditing(false);
      setNotice(
        `已切换为${researchModes.data?.find((item) => item.mode === updated.research_mode)?.label ?? updated.research_mode}。网页、本地研究助手和命令行将读取同一会话配置。`,
      );
      queryClient.invalidateQueries({ queryKey: ["research-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["research-session-detail"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });

  const authorizeToViability = useMutation({
    mutationFn: async () => {
      if (!currentSubjectId || !activeSession?.id) {
        throw new Error("需要先选择明确的策略 subject 和研究会话。");
      }
      const authorization = await apiFetch<ResearchAuthorization>(
        "/api/research-authorizations",
        {
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
        },
      );
      const job = await apiFetch<Job>(
        `/api/research-authorizations/${authorization.id}/start`,
        {
          method: "POST",
          body: JSON.stringify({
            subject_id: authorization.subject_id,
            confirmed_by_user: true,
          }),
        },
      );
      return { authorization, job };
    },
    onSuccess: () => {
      setNotice(
        "研究任务已排队：规则检查 → 小范围试跑 → 快速初筛 → 可行性门槛。后台 Worker 会自动推进，门槛失败时仅进入已授权的廉价诊断。",
      );
      queryClient.invalidateQueries({ queryKey: ["research-authorizations"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
    },
  });

  const startExistingAuthorization = useMutation({
    mutationFn: () => {
      if (!activeAuthorization || !currentSubjectId) {
        throw new Error("没有可启动的有效研究授权。");
      }
      return apiFetch<Job>(
        `/api/research-authorizations/${activeAuthorization.id}/start`,
        {
          method: "POST",
          body: JSON.stringify({
            subject_id: currentSubjectId,
            confirmed_by_user: true,
          }),
        },
      );
    },
    onSuccess: () => {
      setNotice("已授权研究任务已进入后台队列，页面会自动刷新阶段进度。");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["research-authorizations"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
    },
  });

  const approveDirection = useMutation({
    mutationFn: async (proposalId: string) => {
      const current = directions.data?.find((item) => item.id === proposalId);
      if (current?.status === "draft") {
        await apiFetch(`/api/improvement-directions/${proposalId}/submit`, {
          method: "POST",
        });
      }
      return apiFetch(`/api/improvement-directions/${proposalId}/launch-batch`, {
        method: "POST",
        body: JSON.stringify({
          subject_id: proposalId,
          confirmed_by_user: true,
        }),
      });
    },
    onSuccess: () => {
      setNotice(
        "已批准明确方向和预算，并自动创建不可变候选、试验计划与有限批量任务；冻结基准未被覆盖。",
      );
      queryClient.invalidateQueries({ queryKey: ["improvement-directions"] });
      queryClient.invalidateQueries({ queryKey: ["experiment-plans"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["batch-summary"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      queryClient.invalidateQueries({ queryKey: ["research-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["research-session-detail"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
    },
  });
  const materializeComponentHypothesis = useMutation({
    mutationFn: (hypothesisId: string) =>
      apiFetch<ImprovementDirection>(
        `/api/component-hypotheses/${hypothesisId}/materialize-proposal`,
        {
          method: "POST",
          body: JSON.stringify({
            subject_id: hypothesisId,
            confirmed_by_user: true,
          }),
        },
      ),
    onSuccess: () => {
      setNotice(
        "已把诊断假设转换成待审阅改进方案。Baseline 未改变，也尚未创建 Candidate 或运行参数测试。",
      );
      queryClient.invalidateQueries({ queryKey: ["improvement-directions"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
    onError: (error) => {
      setNotice(
        error instanceof Error
          ? error.message
          : "当前诊断假设还不能形成可执行方案，请让本地研究助手补全。",
      );
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
  const launchCandidateValidation = useMutation({
    mutationFn: (planId: string) =>
      apiFetch<Job>(
        `/api/experiment-plans/${planId}/launch-candidate-validation`,
        {
          method: "POST",
          body: JSON.stringify({
            subject_id: planId,
            confirmed_by_user: true,
          }),
        },
      ),
    onSuccess: () => {
      setNotice(
        "候选验证已排队：成本敏感性、三个滚动窗口、参数 ±10% 扰动与可观察行情拆分；最终保留测试仍未使用。",
      );
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["run-bundles"] });
      queryClient.invalidateQueries({
        queryKey: ["candidate-validation-summary"],
      });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });
  const launchLockedTest = useMutation({
    mutationFn: (planId: string) =>
      apiFetch<Job>(
        `/api/experiment-plans/${planId}/launch-locked-test`,
        {
          method: "POST",
          body: JSON.stringify({
            subject_id: planId,
            confirmed_by_user: true,
          }),
        },
      ),
    onSuccess: () => {
      setNotice(
        "最终保留测试已明确批准并排队。该区间只运行一次，不参与参数搜索；结果不会自动晋升策略。",
      );
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["run-bundles"] });
      queryClient.invalidateQueries({
        queryKey: ["candidate-validation-summary"],
      });
      queryClient.invalidateQueries({ queryKey: ["research-budget"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
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

  const updateAssistantEntry = useMutation({
    mutationFn: async (mode: AssistantEntryMode) => {
      setAssistantModeOverride(mode);
      if (!activeSession) return null;
      return apiFetch<Session>(
        `/api/research/sessions/${activeSession.id}/assistant-entry`,
        {
          method: "PATCH",
          body: JSON.stringify({ assistant_entry_mode: mode }),
        },
      );
    },
    onSuccess: (updated) => {
      if (updated) {
        setSession(updated);
        setAssistantModeOverride(null);
      }
      queryClient.invalidateQueries({ queryKey: ["research-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["research-session-detail"] });
    },
    onError: () => {
      setAssistantModeOverride(null);
    },
  });

  const configureProvider = useMutation({
    mutationFn: () =>
      apiFetch<ProviderStatus>("/api/agent/provider", {
        method: "POST",
        body: JSON.stringify({
          provider_name: providerName,
          base_url: providerBaseUrl,
          model: providerModel,
          api_key: providerApiKey,
        }),
      }),
    onSuccess: () => {
      setProviderApiKey("");
      setNotice("网页模型已在当前 API 进程内存中启用；现在可以提交策略。");
      queryClient.invalidateQueries({ queryKey: ["provider-status"] });
      queryClient.invalidateQueries({ queryKey: ["agent-status"] });
      queryClient.invalidateQueries({ queryKey: ["project-status"] });
    },
  });

  const clearProvider = useMutation({
    mutationFn: () =>
      apiFetch<ProviderStatus>("/api/agent/provider", {
        method: "DELETE",
      }),
    onSuccess: () => {
      setProviderApiKey("");
      setNotice("网页模型内存配置已清除。");
      queryClient.invalidateQueries({ queryKey: ["provider-status"] });
      queryClient.invalidateQueries({ queryKey: ["agent-status"] });
      queryClient.invalidateQueries({ queryKey: ["project-status"] });
    },
  });

  const saveIntake = useMutation({
    mutationFn: async () => {
      const values = intakeSchema.parse({ title, sourceType, content });
      if (assistantMode === "external_agent_direct") {
        throw new Error("直接在 Codex 研究模式不从网页提交策略");
      }
      if (!creatingNewSession && activeDraft) {
        throw new Error("当前研究会话已经包含策略；请先新建研究会话。");
      }
      let targetSession =
        (!creatingNewSession ? activeSession : null) ??
        (await apiFetch<Session>("/api/research/sessions", {
          method: "POST",
          body: JSON.stringify({
            title: values.title,
            assistant_entry_mode: assistantMode,
          }),
        }));
      if (targetSession.assistant_entry_mode !== assistantMode) {
        targetSession = await apiFetch<Session>(
          `/api/research/sessions/${targetSession.id}/assistant-entry`,
          {
            method: "PATCH",
            body: JSON.stringify({ assistant_entry_mode: assistantMode }),
          },
        );
      }
      const createdDraft = await apiFetch<StrategyDraft>(
        `/api/research/sessions/${targetSession.id}/intakes`,
        {
          method: "POST",
          body: JSON.stringify({
            source_type: values.sourceType,
            source_name: values.sourceType === "file" ? "file-intake-placeholder" : null,
            raw_content: values.content,
          }),
        },
      );
      await apiFetch(`/api/research/sessions/${targetSession.id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: values.content }),
      });
      const run = await apiFetch<AgentRun>(
        `/api/strategy-drafts/${createdDraft.id}/agent-runs`,
        {
          method: "POST",
          body: JSON.stringify({ entry_mode: assistantMode }),
        },
      );
      return { session: targetSession, draft: createdDraft, run };
    },
    onSuccess: ({ session: createdSession, draft: created, run }) => {
      setSession(createdSession);
      setCreatingNewSession(false);
      setDraft(created);
      setAssistantModeOverride(null);
      setBaseline(null);
      setContent("");
      setNotice(
        run.status === "queued"
          ? "策略已保存，AI 任务已排队；页面会自动同步运行状态。"
          : "策略已保存，AI 已开始生成结构化提案。",
      );
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      queryClient.invalidateQueries({ queryKey: ["research-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["research-session-detail"] });
      queryClient.invalidateQueries({ queryKey: ["research-handoff"] });
      queryClient.invalidateQueries({ queryKey: ["session-agent-runs"] });
      queryClient.invalidateQueries({ queryKey: ["session-agent-occupancy"] });
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
      updateAssistantEntry.error ??
      freeze.error ??
      authorizeToViability.error ??
      startExistingAuthorization.error ??
      approveDirection.error ??
      reviseDirectionBudget.error ??
      cancelBatchJob.error ??
      launchCandidateValidation.error ??
      launchLockedTest.error ??
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
    launchCandidateValidation.error,
    launchLockedTest.error,
    reviseDirectionBudget.error,
    restoreComponent.error,
    saveIntake.error,
    startExistingAuthorization.error,
    updateAssistantEntry.error,
    updateResearchMode.error,
  ]);
  const providerFormError = useMemo(() => {
    const error = configureProvider.error ?? clearProvider.error;
    return error instanceof Error ? error.message : null;
  }, [clearProvider.error, configureProvider.error]);
  const beginNewResearch = () => {
    const preferredAssistantMode: AssistantEntryMode =
      agent.data?.local_connector.available
        ? "web_local_connector"
        : providerStatus.data?.configured
          ? "web_provider"
          : "external_agent_direct";
    setCreatingNewSession(true);
    setSession(null);
    setDraft(null);
    setBaseline(null);
    setTitle("");
    setContent("");
    setAssistantModeOverride(preferredAssistantMode);
    setResearchModeEditing(false);
    updateWorkflowLocation("strategy");
  };
  const selectResearchSession = (sessionId: string) => {
    const selected = sessions.data?.find((item) => item.id === sessionId);
    setCreatingNewSession(false);
    setSession(selected ?? null);
    setDraft(null);
    setBaseline(null);
    setTitle(selected?.title ?? "");
    setContent("");
    setAssistantModeOverride(null);
    setResearchModeEditing(false);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("stage");
      url.searchParams.delete("view");
      window.history.replaceState(null, "", url);
    }
    setWorkflowLocationInitialized(false);
  };
  const cancelNewResearch = () => {
    const fallback = sessions.data?.[0] ?? null;
    setCreatingNewSession(false);
    setSession(fallback);
    setDraft(null);
    setBaseline(null);
    setTitle(fallback?.title ?? "");
    setContent("");
    setAssistantModeOverride(null);
    setResearchModeEditing(false);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("stage");
      url.searchParams.delete("view");
      window.history.replaceState(null, "", url);
    }
    setWorkflowLocationInitialized(false);
  };
  const actionableDirection = directions.data?.find((item) =>
    ["draft", "waiting_approval"].includes(item.status),
  );
  const recommendation =
    batchSummary.data?.recommendation?.decision === "candidate_validation"
      ? batchSummary.data.recommendation
      : null;
  const primaryAction = (() => {
    if (!activeDraft) {
      return {
        label: "输入并保存策略",
        pending: saveIntake.isPending,
        run: () => updateWorkflowLocation("strategy"),
      };
    }
    if (!draftIsFormalized) {
      return {
        label: "继续完成策略形式化",
        pending: false,
        run: () => updateWorkflowLocation("strategy"),
      };
    }
    if (!currentBaselineId) {
      return {
        label: "冻结基准版本 v0",
        pending: freeze.isPending,
        run: () => freeze.mutate(),
      };
    }
    if (candidateValidationIsRunning) {
      return {
        label: "查看候选验证进度",
        pending: false,
        run: () => updateWorkflowLocation("conclusion"),
      };
    }
    if (candidateValidation.data?.decision_status) {
      return {
        label: "审阅最终研究结论",
        pending: false,
        run: () => updateWorkflowLocation("conclusion"),
      };
    }
    if (batchIsRunning) {
      return {
        label: "查看批量参数进度",
        pending: false,
        run: () => updateWorkflowLocation("diagnosis", "parameters"),
      };
    }
    if (recommendation && activePlan && !candidateValidationJob) {
      return {
        label: "运行有界候选验证",
        pending: launchCandidateValidation.isPending,
        run: () => launchCandidateValidation.mutate(activePlan.id),
      };
    }
    if (!latestViability) {
      if (
        activePipelineJob &&
        ["queued", "running"].includes(activePipelineJob.status)
      ) {
        return {
          label: "查看快速初筛进度",
          pending: false,
          run: () => updateWorkflowLocation("screening"),
        };
      }
      if (
        activePipelineJob &&
        ["failed", "cancelled"].includes(activePipelineJob.status)
      ) {
        return {
          label: "查看停止原因与下一步",
          pending: false,
          run: () => updateWorkflowLocation("overview"),
        };
      }
      if (
        activeAuthorization?.status === "active" &&
        !activePipelineJob &&
        !authorizationStages.data?.length
      ) {
        return {
          label: "启动已授权研究",
          pending: startExistingAuthorization.isPending,
          run: () => startExistingAuthorization.mutate(),
        };
      }
      return {
        label: "授权并开始快速初筛",
        pending: authorizeToViability.isPending,
        run: () => authorizeToViability.mutate(),
      };
    }
    if (actionableDirection) {
      return {
        label: `审阅改进方向（${actionableDirection.estimated_trials ?? "有限"} 个方案）`,
        pending: false,
        run: () => updateWorkflowLocation("diagnosis", "improvements"),
      };
    }
    if (latestViability.status === "failed") {
      return {
        label: "查看失败归因与改进方向",
        pending: false,
        run: () => updateWorkflowLocation("diagnosis", "attribution"),
      };
    }
    return {
      label: "查看深度验证条件",
      pending: false,
      run: () => updateWorkflowLocation("validation", "robustness"),
    };
  })();

  return (
    <div className="min-h-screen p-4 md:p-6">
      <StudioOverview
        strategyTitle={
          creatingNewSession ? title || "新策略研究" : activeSession?.title
        }
        researchMode={cnResearchMode(activeSession?.research_mode ?? "guided")}
        assistantEntry={cnStatus(assistantMode)}
        executionStatus={
          creatingNewSession
            ? "尚未创建会话"
            : activeSession
              ? executionStatus
              : "尚无会话"
        }
        conclusion={topConclusion}
        primaryActionLabel={primaryAction.label}
        primaryActionPending={primaryAction.pending}
        onNext={primaryAction.run}
      />

      {!quickMode ? (
        <WorkflowNavigation
          activeStage={activeStage}
          stages={workflowStages}
          onChange={(stage) =>
            updateWorkflowLocation(
              stage,
              stage === "diagnosis"
                ? diagnosisView
                : stage === "validation"
                  ? validationView
                  : undefined,
            )
          }
        />
      ) : null}

      {!quickMode ? (
        <div className="mt-4 flex justify-end xl:hidden">
        <button
          type="button"
          aria-expanded={controlPanelOpen}
          aria-controls="studio-research-console"
          onClick={() => setControlPanelOpen((open) => !open)}
          className="min-h-11 rounded-xl border border-white/10 bg-[#0a151e] px-4 text-sm text-slate-200 transition hover:border-white/20"
        >
          {controlPanelOpen ? "收起研究控制台" : "打开研究控制台"}
        </button>
        </div>
      ) : null}

      <div className="mt-4 grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <main className="min-w-0">
          {visibleStage === "overview" ? (
            <StageContainer
              eyebrow="研究总览"
              title="从当前结论继续，不必翻找整页卡片"
              description="这里汇总策略所处阶段和关键证据；进入任一阶段只切换视图，不会自动启动任务或越过门禁。"
            >
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <OverviewMetric label="当前建议阶段" value={workflowStageLabel(inferredStage)} />
                <OverviewMetric label="冻结基准" value={currentBaselineId ? "已确认" : "尚未确认"} />
                <OverviewMetric label="可行性门槛" value={cnStatus(latestViability?.status ?? "not_evaluated")} />
                <OverviewMetric label="研究结果包" value={String(runBundles.data?.length ?? 0) + " 份"} />
              </div>
              <div className="mt-5">
                <ResearchHandoffPanel
                  handoff={handoff.data}
                  versioningAvailable={Boolean(versioning.data?.available)}
                />
              </div>
              {candidateValidation.data?.available ? (
                <div className="mt-5">
                  <CandidateValidationResultCard
                    summary={candidateValidation.data}
                    onLaunchLockedTest={
                      activePlan
                        ? () => launchLockedTest.mutate(activePlan.id)
                        : undefined
                    }
                    lockedTestPending={launchLockedTest.isPending}
                  />
                </div>
              ) : null}
            </StageContainer>
          ) : null}

          {visibleStage === "strategy" ? (
            <StageContainer
              eyebrow="阶段 1"
              title="策略与基准"
              description="先保留原始策略，核对结构化状态，再人工确认不可覆盖的冻结基准。"
            >
              <div className="space-y-4">
                {!activeDraft ? (
                  <IntakeComposer
                    title={title}
                    sourceType={sourceType}
                    content={content}
                    latestAgentRun={latestAgentRun}
                    assistantMode={assistantMode}
                    agentStatus={agent.data}
                    providerStatus={providerStatus.data}
                    providerName={providerName}
                    providerBaseUrl={providerBaseUrl}
                    providerModel={providerModel}
                    providerApiKey={providerApiKey}
                    providerPending={
                      configureProvider.isPending || clearProvider.isPending
                    }
                    providerError={providerFormError}
                    notice={notice}
                    pending={saveIntake.isPending}
                    error={formError}
                    onAssistantModeChange={(mode) =>
                      updateAssistantEntry.mutate(mode)
                    }
                    onProviderNameChange={setProviderName}
                    onProviderBaseUrlChange={setProviderBaseUrl}
                    onProviderModelChange={setProviderModel}
                    onProviderApiKeyChange={setProviderApiKey}
                    onConfigureProvider={() => configureProvider.mutate()}
                    onClearProvider={() => clearProvider.mutate()}
                    onTitleChange={setTitle}
                    onSourceTypeChange={setSourceType}
                    onContentChange={setContent}
                    onSubmit={() => saveIntake.mutate()}
                  />
                ) : (
                  <div className="flex flex-col gap-3 rounded-2xl border border-white/[0.08] bg-black/10 p-4 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <div className="text-sm font-medium text-slate-200">
                        当前会话的策略已经建立
                      </div>
                      <p className="mt-1 text-xs leading-5 text-slate-500">
                        一个研究会话只保存一份策略。继续查看下方结构化规则；研究另一份策略时请新建会话。
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={beginNewResearch}
                      className="min-h-11 shrink-0 cursor-pointer rounded-xl border border-emerald-300/30 bg-emerald-300/10 px-4 text-sm font-medium text-emerald-100 transition hover:bg-emerald-300/15"
                    >
                      新建策略研究
                    </button>
                  </div>
                )}
                <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="结构化策略">
            <div className="space-y-3 text-sm">
              <Meta label="策略草稿" value={activeDraft ? "已创建" : "未创建"} />
              <Meta label="来源" value={cnSource(activeDraft?.source_type ?? "—")} />
              <Meta label="状态" value={cnStatus(activeDraft?.status ?? "—")} />
              <Meta
                label="规则确认"
                value={draftIsFormalized ? "已确认" : "待确认"}
              />
              {activeDraft && !activeDraft.baseline_version_id ? (
                <>
                  {draftIsFormalized ? (
                    <>
                      <label
                        htmlFor="strategy-formalization"
                        className="block text-xs leading-5 text-slate-400"
                      >
                        Codex 或研究助手已经写回结构化规则。请先审阅；看不懂字段时不要直接修改。
                      </label>
                      <textarea
                        id="strategy-formalization"
                        value={defaultFormalizationText}
                        readOnly
                        rows={10}
                        spellCheck={false}
                        className="w-full rounded-xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs leading-5 text-slate-200 outline-none focus:border-emerald-300/50 focus:ring-2 focus:ring-emerald-300/10"
                      />
                      <div className="rounded-xl border border-emerald-300/20 bg-emerald-300/[0.06] p-3 text-xs leading-5 text-emerald-100">
                        结构化规则已确认。下一步可冻结不可覆盖的 Baseline v0；冻结仍需要单独点击批准。
                      </div>
                    </>
                  ) : (
                    <div className="rounded-xl border border-amber-300/20 bg-amber-300/[0.06] p-4 text-xs leading-5 text-slate-300">
                      <div className="font-medium text-amber-100">
                        等待 Codex 完成策略形式化
                      </div>
                      <p className="mt-2">
                        当前没有真实的结构化规则，所以网页不会预填一份“看起来能确认”的 JSON，也不能冻结基准。请使用上方交接卡把任务交给 Codex。
                      </p>
                    </div>
                  )}
                </>
              ) : null}
              {activeDraft ? (
                <TechnicalDetails>
                  <TechnicalId label="策略草稿" value={activeDraft.id} />
                </TechnicalDetails>
              ) : null}
            </div>
          </Panel>
          <Panel title="基准确认">
            <div className="mb-3 text-xs leading-5 text-slate-400">
              基准冻结后不可覆盖；策略差异、实验、参数方案与报告将作为独立研究证据展示。
            </div>
            <button
              type="button"
              disabled={
                !activeDraft ||
                !draftIsFormalized ||
                Boolean(activeDraft.baseline_version_id) ||
                freeze.isPending
              }
              onClick={() => freeze.mutate()}
              className="min-h-11 w-full rounded-xl border border-emerald-300/30 bg-emerald-300/10 px-3 py-2.5 text-sm font-medium text-emerald-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {currentBaselineId
                ? "基准版本 v0 已冻结"
                : !draftIsFormalized
                  ? "请先确认结构化规则"
                  : freeze.isPending
                    ? "正在冻结基准…"
                    : "冻结基准版本 v0"}
            </button>
          </Panel>
                </div>
              </div>
            </StageContainer>
          ) : null}

          {visibleStage === "screening" ? (
            <StageContainer
              eyebrow="阶段 2"
              title="快速初筛"
              description="选择验证深度，按规则检查、小范围试跑、快速初筛和可行性门槛判断是否值得继续投入。"
            >
              <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="本次验证流程">
            <label className="mb-2 block text-xs text-slate-500" htmlFor="pipeline-profile">
              验证深度
            </label>
            <select
              id="pipeline-profile"
              value={pipelineProfileId}
              onChange={(event) => setPipelineProfileId(event.target.value)}
              className="mb-3 min-h-11 w-full rounded-lg border border-white/10 bg-[#071017] px-3 text-xs text-slate-200"
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
                        {cnStatus(result?.status ?? "not_evaluated")} ·{" "}
                        {stage.stop_on_fail ? "未通过则停止" : "未通过仍可继续"}
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
                <div className="space-y-1.5 leading-5 text-slate-400">
                  {latestViability.reasons.map((reason) => (
                    <div key={reason}>{cnGateReason(reason)}</div>
                  ))}
                </div>
                <TechnicalDetails label="原始判定字段">
                  {latestViability.reasons.map((reason) => (
                    <p key={reason}>{reason}</p>
                  ))}
                </TechnicalDetails>
              </div>
            ) : (
              <Empty>尚无可行性结果；“只比基准少亏”不会成为可用策略候选。</Empty>
            )}
            <div className="mt-3 border-t border-white/[0.06] pt-3 text-xs text-slate-400">
              策略结论：<span className="text-slate-200">{cnOutcome(latestOutcome?.outcome_type ?? "未记录")}</span>
            </div>
          </Panel>
              </div>
            </StageContainer>
          ) : null}

          {visibleStage === "diagnosis" ? (
            <StageContainer
              eyebrow="阶段 3"
              title="诊断与优化"
              description="按问题切换视图：先读真实证据，再做亏损归因，最后形成有预算边界的改进方案。"
            >
              <StageTabs<DiagnosisView>
                label="诊断与优化视图"
                active={diagnosisView}
                items={[
                  { id: "performance", label: "走势与风险", description: "行情、资金、回撤" },
                  { id: "parameters", label: "参数试验", description: "批量结果与敏感性" },
                  { id: "regimes", label: "行情适配", description: "适合与不适合" },
                  { id: "attribution", label: "问题诊断", description: "亏损与多周期漏斗" },
                  { id: "improvements", label: "改进方案", description: "组件、方向、实验" },
                ]}
                onChange={(view) => updateWorkflowLocation("diagnosis", view)}
              />

              {diagnosisView === "performance" ||
              diagnosisView === "parameters" ||
              diagnosisView === "regimes" ? (
                <Panel title="研究图表与指标">
                  <ResearchAnalysis
                    bundles={runBundles.data ?? []}
                    trials={trials.data ?? []}
                    batchSummary={batchSummary.data}
                    regimeMetrics={regimeMetrics}
                    regimeEvidenceStatus={
                      diagnosticReport.data?.regime_diagnostic.evidence_status ??
                      latestRegime?.evidence_status ??
                      "screening"
                    }
                    formalRegimeValidation={latestRegime?.mode === "regime_validation"}
                    activeView={diagnosisView}
                    onViewChange={(view) => updateWorkflowLocation("diagnosis", view)}
                    showNavigation={false}
                  />
                </Panel>
              ) : null}

              {diagnosisView === "attribution" ? (
                <div className="space-y-4">
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
                </div>
              ) : null}

              {diagnosisView === "improvements" ? (
                <div className="space-y-4">
          <Panel title="失败策略中的局部改进">
            <ComponentLibrary
              hypotheses={componentHypotheses.data ?? []}
              candidates={components.data ?? []}
              archivedCandidates={(allComponents.data ?? []).filter(
                (item) => item.archived_at !== null,
              )}
              evidence={componentEvidence.data ?? []}
              materializedHypothesisIds={
                new Set(
                  (directions.data ?? [])
                    .map(
                      (item) =>
                        item.content.source_component_hypothesis_id,
                    )
                    .filter(
                      (item): item is string =>
                        typeof item === "string",
                    ),
                )
              }
              materializePending={
                materializeComponentHypothesis.isPending
              }
              onMaterialize={(hypothesisId) =>
                materializeComponentHypothesis.mutate(hypothesisId)
              }
              onArchive={(candidateId) => archiveComponent.mutate(candidateId)}
              onRestore={(candidateId) => restoreComponent.mutate(candidateId)}
            />
          </Panel>
          <Panel title="改进方向（最多 3 个）">
            <ImprovementDirections
              directions={directions.data ?? []}
              providerConfigured={Boolean(project.data?.ai_provider.configured)}
              pending={
                approveDirection.isPending ||
                reviseDirectionBudget.isPending
              }
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
          <Panel title="策略版本关系">
            <StrategyVersionLineage
              versions={strategyVersions.data ?? []}
              directions={directions.data ?? []}
              plans={plans.data ?? []}
              trials={trials.data ?? []}
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
                </div>
              ) : null}
            </StageContainer>
          ) : null}

          {visibleStage === "validation" ? (
            <StageContainer
              eyebrow="阶段 4"
              title="深度验证"
              description="只有同一策略版本通过可行性门槛后，才进入跨期稳健性、第二引擎对账和 TradingView 语义核对。"
            >
              <StageTabs<ValidationView>
                label="深度验证视图"
                active={validationView}
                items={[
                  { id: "robustness", label: "稳健性验证", description: "跨期、压力、锁定测试" },
                  { id: "reconciliation", label: "第二引擎对账", description: "逐笔一致性" },
                  { id: "tradingview", label: "TradingView", description: "Pine 语义与对账" },
                ]}
                onChange={(view) => updateWorkflowLocation("validation", view)}
              />

              {validationView === "robustness" ? (
                <Panel title="稳健性验证状态">
                  <div className="space-y-3 text-xs">
                    <Meta
                      label="进入条件"
                      value={latestViability?.status === "passed" ? "可行性门槛已通过" : "可行性门槛尚未通过"}
                    />
                    <Meta
                      label="多周期信号"
                      value={validationFunnel ? "已有 5m / 15m / 1h 诊断证据" : "尚无独立证据"}
                    />
                    <Meta label="滚动样本外" value="尚未执行" />
                    <Meta label="完整压力测试" value="尚未执行" />
                    <Meta label="最终保留测试" value="尚未执行，禁止用于调参" />
                    <div className="rounded-lg border border-dashed border-white/15 p-3 leading-5 text-slate-500">
                      本页只报告真实状态。初筛失败时不会为了补齐界面而伪造深度验证结果。
                    </div>
                  </div>
                </Panel>
              ) : null}

              {validationView === "reconciliation" ? (
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
              ) : null}

              {validationView === "tradingview" ? (
                <Panel title="TradingView / Pine 验证">
                  <div className="space-y-3 text-xs">
                    <Meta label="策略来源" value={cnSource(activeDraft?.source_type ?? "—")} />
                    <Meta label="早期检查" value="Pine 语义、重绘、多周期与代表交易" />
                    <Meta
                      label="完整对账"
                      value={latestViability?.status === "passed" ? "门槛通过后可安排" : "等待可行性门槛"}
                    />
                    <div className="rounded-lg border border-dashed border-white/15 p-3 leading-5 text-slate-500">
                      Pine 来源策略应先完成语义与重绘风险检查；完整 TradingView 逐笔对账不在失败策略上重复消耗时间。
                    </div>
                  </div>
                </Panel>
              ) : null}
            </StageContainer>
          ) : null}

          {visibleStage === "conclusion" ? (
            <StageContainer
              eyebrow="阶段 5"
              title="结论与归档"
              description="统一查看当前策略结论、研究结果包和追加式时间线；生产晋升仍需人工批准。"
            >
              <div className="mb-4">
                <ResearchDecisionSheet
                  conclusion={topConclusion}
                  viabilityStatus={latestViability?.status}
                  viabilityReasons={
                    latestViability?.reasons.map(cnGateReason) ?? []
                  }
                  regimeGroups={regimeGroups}
                  batchSummary={batchSummary.data}
                  candidateValidation={candidateValidation.data}
                  componentCandidateCount={(components.data ?? []).filter(
                    (item) => item.status === "component_candidate",
                  ).length}
                  reportCount={runBundles.data?.length ?? 0}
                  reconciliation={engineReconciliation.data}
                />
              </div>
              {candidateValidation.data?.available ? (
                <div className="mb-4">
                  <CandidateValidationResultCard
                    summary={candidateValidation.data}
                    onLaunchLockedTest={
                      activePlan
                        ? () => launchLockedTest.mutate(activePlan.id)
                        : undefined
                    }
                    lockedTestPending={launchLockedTest.isPending}
                  />
                </div>
              ) : null}
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
              <div className="mt-4 rounded-xl border border-amber-300/15 bg-amber-300/[0.06] p-4 text-xs leading-5 text-amber-100">
                归档保留失败实验与技术证据；任何策略进入生产或实盘仍需要单独、明确的人工批准。
              </div>
            </StageContainer>
          ) : null}
        </main>

        <aside
          id="studio-research-console"
          aria-label="研究控制台"
          className={
            controlPanelOpen
              ? "min-w-0 space-y-4 xl:block"
              : "hidden min-w-0 space-y-4 xl:block"
          }
        >
          <Panel title="研究会话">
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <label className="text-xs text-slate-500" htmlFor="session-select">
                  当前研究
                </label>
                <button
                  type="button"
                  onClick={
                    creatingNewSession ? cancelNewResearch : beginNewResearch
                  }
                  className="min-h-11 cursor-pointer rounded-lg border border-white/10 px-3 text-xs text-slate-300 transition hover:border-white/20 hover:bg-white/[0.04]"
                >
                  {creatingNewSession ? "取消新建" : "新建研究"}
                </button>
              </div>
              <select
                id="session-select"
                value={creatingNewSession ? "__new__" : activeSession?.id ?? ""}
                onChange={(event) => selectResearchSession(event.target.value)}
                disabled={
                  creatingNewSession &&
                  Boolean(title.trim() || content.trim())
                }
                className="min-h-11 w-full min-w-0 rounded-lg border border-white/10 bg-[#071017] px-2 text-xs text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {creatingNewSession ? (
                  <option value="__new__">新策略研究（尚未保存）</option>
                ) : null}
                {(sessions.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
              {creatingNewSession ? (
                <div className="rounded-lg border border-sky-300/15 bg-sky-300/[0.06] p-3 text-xs leading-5 text-sky-100">
                  正在准备独立会话。选择 AI 入口并输入策略后，系统才会保存新会话；不会写入当前旧策略。
                </div>
              ) : activeSession ? (
                <>
                  <Meta label="状态" value={cnStatus(activeSession.status)} />
                  <Meta label="策略草稿" value={activeDraft ? "1 份" : "0 份"} />
                  <Meta
                    label="当前基准"
                    value={currentBaselineId ? "已冻结" : "未冻结"}
                  />
                  <div
                    className={
                      agentOccupancy.isError || agentOccupancy.isPending
                        ? "rounded-lg border border-white/10 bg-white/[0.03] p-2 text-xs leading-5 text-slate-400"
                        : agentOccupancy.data?.occupied
                          ? "rounded-lg border border-amber-300/20 bg-amber-300/10 p-2 text-xs leading-5 text-amber-100"
                          : "rounded-lg border border-emerald-300/15 bg-emerald-300/[0.07] p-2 text-xs leading-5 text-emerald-100"
                    }
                  >
                    {agentOccupancy.isPending ? (
                      "正在读取当前会话的 AI 占用状态……"
                    ) : agentOccupancy.isError ? (
                      "AI 会话隔离接口尚未加载；重启本地 API 后生效。"
                    ) : agentOccupancy.data?.occupied ? (
                      <>
                        当前由
                        <span className="mx-1 font-medium">
                          {friendlyAgentName(agentOccupancy.data.agent_name)}
                        </span>
                        写入。其他策略请新建研究会话。
                      </>
                    ) : sessionHasActiveJob ? (
                      "当前会话的后台任务仍在运行；可以切换查看其他会话，但不能修改本会话模式。"
                    ) : (
                      "当前会话空闲。不同策略使用不同研究会话，可以安全并行。"
                    )}
                  </div>
                  {currentBaselineId ? (
                    <TechnicalDetails label="查看基准技术 ID">
                      <TechnicalId label="冻结基准" value={currentBaselineId} />
                    </TechnicalDetails>
                  ) : null}
                </>
              ) : (
                <Empty>尚无研究会话，请点击“新建研究”。</Empty>
              )}
            </div>
          </Panel>
          <Panel title="研究模式">
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs text-slate-500">当前交互节奏</div>
                  <div className="mt-1 text-sm font-medium text-slate-200">
                    {cnResearchMode(activeSession?.research_mode ?? "guided")}
                  </div>
                </div>
                {activeSession && !researchModeLocked ? (
                  <button
                    type="button"
                    onClick={() =>
                      setResearchModeEditing((editing) => !editing)
                    }
                    className="min-h-11 cursor-pointer rounded-lg border border-white/10 px-3 text-xs text-slate-300 transition hover:border-white/20 hover:bg-white/[0.04]"
                  >
                    {researchModeEditing ? "取消" : "修改"}
                  </button>
                ) : null}
              </div>
              {researchModeEditing && activeSession && !researchModeLocked ? (
                <>
                  <label htmlFor="research-mode" className="sr-only">
                    选择研究模式
                  </label>
                  <select
                    id="research-mode"
                    value={activeSession.research_mode}
                    onChange={(event) =>
                      updateResearchMode.mutate(
                        event.target.value as "quick" | "guided" | "expert",
                      )
                    }
                    disabled={updateResearchMode.isPending}
                    className="min-h-11 w-full rounded-xl border border-emerald-300/30 bg-[#071017] px-3 text-sm text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {(researchModes.data ?? []).map((item) => (
                      <option key={item.mode} value={item.mode}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </>
              ) : null}
              <div className="rounded-lg bg-white/[0.03] p-3 text-xs leading-5 text-slate-400">
                {researchModes.data?.find(
                  (item) => item.mode === activeSession?.research_mode,
                )?.description ?? "引导模式每轮只在关键决策处询问一次。"}
              </div>
              <div
                className={
                  researchModeLocked
                    ? "rounded-lg border border-amber-300/20 bg-amber-300/[0.06] p-3 text-[11px] leading-5 text-amber-100"
                    : "rounded-lg border border-dashed border-white/15 p-3 text-[11px] leading-5 text-slate-500"
                }
              >
                {researchModeLocked
                  ? "当前 AI 或后台任务已经按启动时的模式执行。任务结束后才能修改，避免页面状态与实际执行不一致。"
                  : creatingNewSession
                    ? "新会话默认使用引导模式；会话保存后可在空闲状态修改。"
                    : "模式只影响下一次任务的交互节奏，不会跳过基准冻结、实验预算、最终测试或实盘门禁。"}
              </div>
            </div>
          </Panel>
          {currentBaselineId && !quickMode ? (
            <Panel title="一次性研究授权">
            {activeAuthorization ? (
              <div className="space-y-3 text-xs">
                <Meta label="状态" value={cnStatus(activeAuthorization.status)} />
                <Meta
                  label="后台任务"
                  value={
                    activePipelineJob
                      ? cnStatus(activePipelineJob.status)
                      : authorizationStages.data?.length
                        ? "已有阶段证据"
                        : "尚未启动"
                  }
                />
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
                    <div>
                      {activePipelineJob?.status === "failed"
                        ? "后台任务已停止。请回到总览查看停止原因和明确下一步；系统不会静默重试。"
                        : activePipelineJob?.status === "cancelled"
                          ? "后台任务已取消，已有证据仍保留。请回到总览决定是否重新授权。"
                          : activePipelineJob
                            ? "任务已进入后台队列，Worker 将自动记录阶段进度。"
                        : "授权已保存，但尚未启动后台研究任务。"}
                    </div>
                  ) : null}
                </div>
                {!activePipelineJob &&
                activeAuthorization.status === "active" &&
                !authorizationStages.data?.length ? (
                  <button
                    type="button"
                    onClick={() => startExistingAuthorization.mutate()}
                    disabled={startExistingAuthorization.isPending}
                    className="min-h-11 w-full rounded-lg border border-emerald-300/30 bg-emerald-300/10 px-3 py-2 text-sm font-medium text-emerald-100 disabled:opacity-40"
                  >
                    {startExistingAuthorization.isPending
                      ? "正在加入后台队列…"
                      : "启动已授权研究"}
                  </button>
                ) : null}
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
                  className="min-h-11 w-full rounded-lg border border-emerald-300/30 bg-emerald-300/10 px-3 py-2 text-sm font-medium text-emerald-100 disabled:opacity-40"
                >
                  {authorizeToViability.isPending
                    ? "正在授权并创建任务…"
                    : "授权并开始研究"}
                </button>
                <div className="text-[11px] leading-4 text-slate-500">
                  页面自动携带当前研究对象；无需复制长 ID。未通过策略只能做已授权廉价诊断。
                </div>
              </div>
            )}
            </Panel>
          ) : null}
          {currentBaselineId && expertMode ? (
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
          ) : null}
          {!quickMode && (activePlan || activeBatchJob) ? (
            <Panel title="批量参数进度">
              <BatchProgressPanel
                summary={batchSummary.data}
                candidateValidation={candidateValidation.data}
                job={activeBatchJob}
                onCancel={(jobId) => cancelBatchJob.mutate(jobId)}
                onValidate={(planId) =>
                  launchCandidateValidation.mutate(planId)
                }
              />
            </Panel>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

function friendlyAgentName(name: string | null) {
  if (!name) return "本地研究助手";
  const normalized = name.toLowerCase();
  if (normalized.includes("codex")) return "Codex 本地研究助手";
  return name;
}

function workflowStageLabel(stage: WorkflowStage) {
  return {
    overview: "总览",
    strategy: "策略与基准",
    screening: "快速初筛",
    diagnosis: "诊断与优化",
    validation: "深度验证",
    conclusion: "结论与归档",
  }[stage];
}

function OverviewMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-2 text-sm font-medium text-slate-100">{value}</div>
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
    <section className="min-w-0 rounded-2xl border border-white/10 bg-[#0a151e]/90 p-4">
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
