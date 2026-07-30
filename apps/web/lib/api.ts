export const API_BASE =
  process.env.NEXT_PUBLIC_QUANT_LAB_API_URL ?? "http://127.0.0.1:8100";

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

export type VersioningPolicy = {
  available: boolean;
  reason?: string;
  policy_id?: string;
  authoritative_strategy_state?: string[];
  git_role?: string;
  git_required_for_local_product?: boolean;
  remote_required?: boolean;
  auto_commit_on_intake?: boolean;
  auto_commit_on_formalization?: boolean;
  auto_commit_on_freeze?: boolean;
  auto_push?: boolean;
  git_allowed_events?: string[];
  local_authoritative_roots?: string[];
  existing_tracked_strategy_history_policy?: string;
  future_local_artifacts_may_remain_uncommitted?: boolean;
  versioned_export_requires_explicit_user_action?: boolean;
  versioned_export_root?: string;
  research_actions_must_not_invoke_git?: boolean;
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

export type ExecutionModelSummary = {
  model_id: string;
  venue: string;
  market_profile: string;
  symbol: string;
  status: string;
  default_leverage: number;
  max_research_leverage: number;
  live_trading_enabled: boolean;
  margin_mode: string;
  mark_price_liquidation: boolean;
  same_bar_priority: string[];
  funding_policy: string;
  leverage_tiers_status: string;
  tier_source: string;
  historical_tiers_complete: boolean;
  precision: {
    tick_size: number;
    quantity_step: number;
    minimum_quantity: number;
    minimum_notional: number;
    quantity_unit: string;
    contract_size_base: number;
  };
  limitations: string[];
};

export type Session = {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  research_mode: "quick" | "guided" | "expert";
  mode_config: {
    agent_run_mode?: "supervised" | "guided" | "bounded_autonomous";
    pause_policy?: "critical_only" | "key_decisions" | "every_stage";
    stage_visibility?: "summary" | "guided" | "full";
    default_trial_budget?: number;
    auto_failure_diagnostics?: boolean;
  };
  mode_revision: number;
};

export type SessionAgentOccupancy = {
  session_id: string;
  occupied: boolean;
  agent_run_id: string | null;
  agent_name: string | null;
  status: string | null;
  plan_summary: string | null;
  started_at: string | null;
  lease_expires_at: string | null;
};

export type ResearchModeDefinition = {
  mode: "quick" | "guided" | "expert";
  label: string;
  description: string;
  agent_run_mode: "supervised" | "guided" | "bounded_autonomous";
  pause_policy: "critical_only" | "key_decisions" | "every_stage";
  stage_visibility: "summary" | "guided" | "full";
  default_trial_budget: number;
  auto_failure_diagnostics: boolean;
};

export type SessionDetail = {
  session: Session;
  messages: Array<{
    id: string;
    session_id: string;
    role: string;
    content: string;
    created_at: string;
  }>;
  drafts: StrategyDraft[];
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

export type StrategyVersion = {
  id: string;
  strategy_id: string;
  version: number;
  status:
    | "baseline"
    | "candidate"
    | "validated"
    | "dry_run"
    | "degraded"
    | "retired"
    | "rejected";
  content_snapshot: Record<string, unknown>;
  source_snapshot: string;
  created_at: string;
  immutable: boolean;
};

export type FactorRegistryItem = {
  factor_id: string;
  name: string;
  category: string;
  version: number;
  status:
    | "candidate"
    | "validated"
    | "production"
    | "degraded"
    | "retired"
    | "rejected";
  formula_path: string | null;
  description: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
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

export type ResearchHandoff = {
  id: string;
  session_id: string;
  agent_run_id: string | null;
  subject_id: string;
  status:
    | "completed_scope"
    | "waiting_user_approval"
    | "waiting_required_input"
    | "gate_failed"
    | "budget_exhausted"
    | "blocked_dependency"
    | "safety_refusal"
    | "failed";
  stop_reason_code: string;
  stop_reason_text: string;
  completed_actions: string[];
  not_started_actions: string[];
  user_action_required: boolean;
  required_user_action: string | null;
  next_recommended_action: string;
  approval_subject_id: string | null;
  safe_to_continue: boolean;
  created_at: string;
};

export type ResearchAuthorization = {
  id: string;
  subject_id: string;
  session_id: string;
  allowed_stages: string[];
  auto_continue: boolean;
  max_cost_usdt: number;
  max_time_minutes: number;
  max_trials: number;
  locked_test_allowed: boolean;
  stop_conditions: string[];
  expires_at: string;
  approved_by: string;
  status: string;
  created_at: string;
  used_cost_usdt: number;
  used_time_minutes: number;
  used_trials: number;
};

export type ResearchAuthorizationStage = {
  id: string;
  authorization_id: string;
  stage: string;
  status: string;
  evidence_refs: string[];
  reason: string;
  elapsed_minutes: number;
  cost_usdt: number;
  trials_used: number;
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
  archived_at: string | null;
  archive_reason: string | null;
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

export type ComponentHypothesis = {
  id: string;
  session_id: string;
  subject_id: string;
  title: string;
  hypothesis: string;
  component_type: string;
  source: string;
  evidence_refs: string[];
  expected_improvement: string;
  parameter_space: Record<string, unknown>;
  suggested_trials: number;
  failure_conditions: string[];
  evidence_level: string;
  contamination_status: string;
  status: string;
  created_at: string;
};

export type RunBundle = {
  bundle_id: string;
  job_id: string;
  subject_id: string | null;
  job_type: string;
  status: string;
  report_type: string;
  report_artifact_key: string;
  summary: Record<string, unknown>;
  retention: Record<string, string>;
  created_at: string;
};

export type ResearchDiagnosticReport = {
  run_id: string;
  subject_id: string;
  strategy_status_remains: "rejected";
  loss_attribution: {
    reran_strategy: false;
    causal_claim_allowed: false;
    splits: Record<
      string,
      {
        summary: Record<string, number>;
        by_side: Record<string, Record<string, number>>;
        by_exit_reason: Record<string, Record<string, number>>;
        by_holding_period: Record<string, Record<string, number>>;
        by_entry_utc_session: Record<string, Record<string, number>>;
        by_entry_weekday: Record<string, Record<string, number>>;
        by_stop_distance: Record<string, Record<string, number>>;
        by_first_entry_or_reentry: Record<string, Record<string, number>>;
        costs: Record<string, number | null>;
        streaks: Record<string, number>;
      }
    >;
    signal_funnel: Record<
      string,
      {
        trend_1h: {
          trend_leg_count?: number;
          bar_counts_by_direction?: Record<string, number>;
          directions: string[];
        };
        pullback_candidates_15m: { count: number | null; availability: string };
        confirmations_15m: number | null;
        confirmations_15m_availability?: string;
        trigger_records_5m: number;
        filled_entries: number;
        signal_status_counts: Record<string, number>;
        filter_or_cancel_reasons: Record<string, number>;
      }
    >;
    multi_timeframe: {
      executed: boolean;
      timeframes: string[];
      walk_forward_or_multi_period_executed: boolean;
    };
  };
  regime_diagnostic: {
    mode: "regime_diagnostic";
    evidence_status: string;
    regime_metrics: Record<string, Record<string, number>>;
    groups: Record<string, string[]>;
    formal_validation: false;
  };
  component_hypotheses: Array<{
    id: string;
    title: string;
    component_type: string;
    source: string;
    suggested_trials: number;
    status: string;
    contamination_status: string;
  }>;
  research_boundaries: Record<string, boolean>;
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
  job_id?: string | null;
  job_status?: string;
  total_trials?: number;
  completed_trials?: number;
  succeeded_trials?: number;
  failed_trials?: number;
  cancelled_trials?: number;
  running_trials?: number;
  queued_trials?: number;
  remaining_trials?: number;
  concurrency?: number;
  elapsed_seconds?: number;
  peak_rss_mb?: number;
  stop_reason?: string | null;
  continue_reason?: string | null;
};

export type EquityChartSeries = {
  series_id: string;
  label: string;
  kind: string;
  points: Array<{
    t: string;
    normalized_equity: number;
    drawdown: number;
  }>;
  source_artifact_key: string;
  evidence_mode: string;
};

export type MarketChartSeries = {
  series_id: string;
  label: string;
  kind: "market_price";
  unit: "quote_price";
  timeframe: string;
  source_timeframe: string;
  aggregated: boolean;
  points: Array<{
    t: string;
    value: number;
  }>;
  candles: Array<{
    t: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
  source_artifact_keys: string[];
  evidence_mode: "market_context";
};

export type TradeChartRecord = {
  trade_id: string;
  split: string;
  side: "long" | "short" | "unknown";
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  stop_price: number | null;
  take_profit_price: number | null;
  net_return: number | null;
  net_pnl: number | null;
  exit_reason: string;
  source_artifact_key: string;
};

export type RunBundleChart = {
  available: boolean;
  bundle_id: string;
  series: EquityChartSeries[];
  market_series: MarketChartSeries | null;
  market_reason?: string | null;
  available_market_timeframes: string[];
  trades: TradeChartRecord[];
  trade_source_artifact_keys: string[];
  reason?: string;
  limitations: string[];
};

export type EngineReconciliationStatus = {
  subject_id: string;
  market_profile: string;
  viability_gate_result_id: string | null;
  eligible: boolean;
  engine_id: string;
  engine_boundary: "optional_external_process";
  implementation_status: "not_connected";
  job_created: false;
  reason: string;
  locked_test_allowed: false;
  live_trade_available: false;
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
