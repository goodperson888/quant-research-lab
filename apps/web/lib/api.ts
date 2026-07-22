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
};

export type RegimeValidation = {
  id: string;
  subject_type: string;
  subject_id: string;
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
  created_at: string;
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
