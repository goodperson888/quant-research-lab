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
