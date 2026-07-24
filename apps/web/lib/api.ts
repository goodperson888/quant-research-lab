export const API_BASE =
  process.env.NEXT_PUBLIC_QUANT_LAB_API_URL ?? "http://127.0.0.1:8000";

export type ProjectStatus = {
  name: string;
  mode: string;
  product_stage: string;
  git: { branch: string | null; revision: string | null };
  safety: {
    live_trading_enabled: boolean;
    trade_api_available: boolean;
    production_promotion_available: boolean;
  };
  ai_provider: { configured: boolean; provider: string | null; message: string };
  market: {
    requested_primary: string;
    effective_data_source: string;
    instrument: string;
    timezone: string;
  };
};

export type AgentStatus = {
  architecture: string;
  default_run_mode: string;
  active_configuration: {
    agent_provider: string;
    execution_target: string;
    data_location: string;
    privacy_note: string;
  };
  external_agent: {
    supported: boolean;
    preferred_for_phase_0: boolean;
    connection_status: string;
    note: string;
  };
  embedded_provider: {
    configured: boolean;
    provider: string | null;
    message: string;
  };
  model_policy: AgentManifestSummary;
};

export type AgentManifestSummary = {
  available: boolean;
  reason?: string;
  policy_id?: string;
  minimum_context_tokens?: number;
  weak_model_fallback_allowed?: boolean;
  provider_configured: boolean;
};

export type AgentManifest = {
  available: boolean;
  reason?: string;
  provider_configured: boolean;
  policy_id?: string;
  status?: string;
  minimum_capabilities?: {
    native_tool_calling: boolean;
    json_schema_structured_output: boolean;
    multi_turn_tool_results: boolean;
    minimum_context_tokens: number;
    instruction_hierarchy: boolean;
    languages: string[];
  };
  forbidden_compatibility_modes?: string[];
  adapter_priority?: string[];
};

export type WorkerResourcePolicy = {
  available: boolean;
  reason?: string;
  policy_id?: string;
  max_rss_mb?: number;
  max_concurrent_trials?: number;
  max_job_minutes?: number;
  parquet_batch_rows?: number;
  kill_on_memory_limit?: boolean;
};

export type Session = {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type StrategyDraft = {
  id: string;
  session_id: string;
  source_type: string;
  source_name: string | null;
  raw_content: string;
  structured_content: Record<string, unknown>;
  status: string;
  baseline_version_id: string | null;
  created_at: string;
};

export type Job = {
  id: string;
  job_type: string;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  error: string | null;
};

export type AuditEvent = {
  id: number;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  actor_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type PipelineProfile = {
  id: string;
  label: string;
  description: string;
  candidate_eligible: boolean;
  stages: Array<{
    id: string;
    gate: string;
    approval: string;
    stop_on_fail: boolean;
    outputs: string[];
  }>;
  gates: Record<string, { requires: string[] }>;
  acceptance_policies: Record<string, unknown>;
  pine_validation: {
    natural_language_or_python: { full_reconciliation_after: string };
    pine_source: { early_checks: string[]; full_diagnostic_after: string };
  };
};

export type GateEvaluation = {
  id: string;
  profile_id: string;
  gate_name: string;
  subject_type: string;
  subject_id: string;
  market_profile: string;
  strategy_objective: string;
  status: "passed" | "failed" | "blocked" | "not_evaluated";
  metrics: Record<string, number>;
  reasons: string[];
  created_at: string;
};

export type StrategyOutcome = {
  id: string;
  strategy_version_id: string;
  market_profile: string;
  pipeline_profile_id: string;
  outcome_type: "diagnostic_improvement" | "strategy_candidate" | "validated" | "rejected";
  viability_gate_result_id: string | null;
  evidence_artifact_keys: string[];
  notes: string;
  created_at: string;
};

export type ComponentCandidate = {
  id: string;
  evidence_id: string;
  name: string;
  status: "diagnostic_improvement" | "component_candidate" | "rejected";
  created_at: string;
  logic_signature: string;
  target_market_profile: string;
  timeframe: string;
};

export type ComponentEvidence = {
  id: string;
  source_strategy_version_id: string;
  lineage: Record<string, unknown>;
  component_type: string;
  target_market_profile: string;
  incremental_metrics: Record<string, number>;
  out_of_sample_status: string;
  failure_conditions: Array<Record<string, unknown>>;
  created_at: string;
  logic_signature: string;
  timeframe: string;
  source_experiment_plan_id: string | null;
  source_trial_ids: string[];
  stable_parameter_ranges: Record<string, unknown>;
  failed_parameter_ranges: Record<string, unknown>;
  regimes: string[];
  evidence_level: string;
};

export type ImprovementDirection = {
  id: string;
  draft_id: string;
  proposal_type: string;
  content: Record<string, unknown>;
  status:
    | "draft"
    | "waiting_approval"
    | "approved"
    | "executing"
    | "evaluated"
    | "accepted"
    | "rejected"
    | "expired";
  baseline_version_id: string | null;
  subject_id: string | null;
  hypothesis: string;
  rule_diff: Record<string, unknown>;
  evidence_refs: string[];
  parameter_space: Array<{
    name: string;
    kind: string;
    values: unknown[];
    lower: number | null;
    upper: number | null;
    step: number | null;
  }>;
  data_splits: Record<string, string>;
  cost_model: Record<string, unknown>;
  objectives: Array<{ metric: string; direction: string }>;
  constraints: Array<{ metric: string; operator: string; value: number }>;
  estimated_trials: number | null;
  estimated_minutes: number | null;
  failure_conditions: string[];
  stopping_conditions: string[];
  rollback_plan: string;
  candidate_version_id: string | null;
  created_at: string;
};

export type ExperimentPlan = {
  id: string;
  baseline_version_id: string;
  hypothesis: string;
  parameter_space: ImprovementDirection["parameter_space"];
  objectives: Array<{ metric: string; direction: string }>;
  constraints: Array<{ metric: string; operator: string; value: number }>;
  data_splits: Record<string, string>;
  cost_model: Record<string, unknown>;
  max_trials: number | null;
  time_budget_seconds: number | null;
  stopping_conditions: string[];
  proposal_id: string | null;
  candidate_version_id: string | null;
  search_strategy: "grid" | "random";
  random_seed: number;
  status: string;
  approved_by: string | null;
  created_at: string;
};

export type Trial = {
  id: string;
  experiment_plan_id: string;
  parameters: Record<string, unknown>;
  data_version: string;
  status: string;
  metrics: Record<string, number>;
  candidate_version_id: string | null;
  parameter_signature: string | null;
  split: string;
  seed: number;
  error: string | null;
  elapsed_seconds: number | null;
  peak_rss_mb: number | null;
  metrics_artifact_key: string | null;
};

export type BatchSummary = {
  experiment_plan_id: string;
  baseline_version_id: string;
  candidate_version_id: string | null;
  search_strategy: string;
  trial_count: number;
  succeeded_count: number;
  stable_count: number;
  stable_parameter_ranges: Record<string, unknown>;
  failed_parameter_ranges: Record<string, unknown>;
  representative_stable_metrics: Record<string, number>;
  cost_model: Record<string, unknown>;
  baseline_metrics: Record<string, number> | null;
  evidence_mode: "research" | "fixture" | "unavailable";
  research_conclusion_allowed: boolean;
  locked_test_used: boolean;
};

export type RegimeValidation = {
  id: string;
  subject_type: string;
  subject_id: string;
  mode: "regime_diagnostic" | "regime_validation";
  market_profile: string;
  detector_version: string;
  ex_ante_observable: boolean;
  target_regimes: string[];
  suitable_regimes: string[];
  conditional_regimes: string[];
  blocked_regimes: string[];
  unknown_regimes: string[];
  regime_metrics: Record<string, Record<string, number>>;
  transition_policy: Record<string, unknown>;
  history_days: number;
  evidence_status: "screening" | "insufficient_history" | "extended_validation";
  viability_gate_result_id: string | null;
  created_at: string;
};

export type ResearchBudget = {
  session_id: string;
  max_hypotheses: number;
  max_trials_total: number;
  max_compute_minutes: number;
  max_locked_test_uses: number;
  require_user_approval_for_new_hypothesis: boolean;
  used_hypotheses: number;
  reserved_trials: number;
  reserved_compute_minutes: number;
  used_locked_test_uses: number;
  remaining_hypotheses: number;
  remaining_trials: number;
  remaining_compute_minutes: number;
  remaining_locked_test_uses: number;
};

export type ResearchBudgetPolicy = {
  available: boolean;
  reason?: string;
  policy_id?: string;
  max_hypotheses?: number;
  max_trials_total?: number;
  max_compute_minutes?: number;
  max_locked_test_uses?: number;
  require_user_approval_for_new_hypothesis?: boolean;
};

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `API request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}
