"use client";

import { useQuery } from "@tanstack/react-query";

import {
  AgentManifest,
  AgentStatus,
  apiFetch,
  ExecutionModelSummary,
  ProjectStatus,
  VersioningPolicy,
  WorkerResourcePolicy,
} from "@/lib/api";

export function SettingsStatus() {
  const project = useQuery({
    queryKey: ["project-status"],
    queryFn: () => apiFetch<ProjectStatus>("/api/project/status"),
  });
  const agent = useQuery({
    queryKey: ["agent-status"],
    queryFn: () => apiFetch<AgentStatus>("/api/agent/status"),
  });
  const manifest = useQuery({
    queryKey: ["agent-manifest"],
    queryFn: () => apiFetch<AgentManifest>("/api/agent/manifest"),
  });
  const workerPolicy = useQuery({
    queryKey: ["worker-resource-policy"],
    queryFn: () => apiFetch<WorkerResourcePolicy>("/api/worker/resource-policy"),
  });
  const versioning = useQuery({
    queryKey: ["versioning-policy"],
    queryFn: () => apiFetch<VersioningPolicy>("/api/versioning/policy"),
  });
  const executionModels = useQuery({
    queryKey: ["execution-models"],
    queryFn: () => apiFetch<ExecutionModelSummary[]>("/api/execution-models"),
  });
  if (project.isPending || agent.isPending || manifest.isPending || workerPolicy.isPending || versioning.isPending || executionModels.isPending)
    return <div className="text-sm text-slate-500">读取本地设置…</div>;
  if (project.isError || agent.isError || manifest.isError || workerPolicy.isError || versioning.isError || executionModels.isError)
    return <div className="text-sm text-rose-300">API未连接，无法读取真实状态。</div>;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title="当前运行组合">
        <Row label="Agent Provider" value={agent.data.active_configuration.agent_provider} />
        <Row label="Execution Target" value={agent.data.active_configuration.execution_target} />
        <Row label="Agent Run Mode" value={agent.data.default_run_mode} />
        <Row label="数据位置" value={agent.data.active_configuration.data_location} />
      </Section>
      <Section title="AI Provider">
        <Row label="Embedded Provider" value={project.data.ai_provider.configured ? "configured" : "未配置"} />
        <p className="mt-3 text-xs leading-5 text-slate-500">{project.data.ai_provider.message}</p>
      </Section>
      <Section title="模型能力基线">
        {manifest.data.available && manifest.data.minimum_capabilities ? (
          <>
            <Row label="策略" value={manifest.data.policy_id ?? "unknown"} />
            <Row label="原生 Tool Calling" value="required" />
            <Row label="JSON Schema 输出" value="required" />
            <Row label="多轮 Tool Results" value="required" />
            <Row
              label="最小上下文"
              value={`≥ ${manifest.data.minimum_capabilities.minimum_context_tokens / 1024}K tokens`}
            />
            <Row label="指令层级" value="required" />
            <Row
              label="语言"
              value={manifest.data.minimum_capabilities.languages.join(" / ")}
            />
            <p className="mt-3 text-xs leading-5 text-amber-200/80">
              无 chat-only、提示词模拟工具、free-text JSON 修补或静默弱模型降级。
            </p>
          </>
        ) : (
          <p className="text-sm text-amber-200/80">
            模型能力契约不可用：{manifest.data.reason ?? "unknown"}。系统不会伪造已配置状态。
          </p>
        )}
      </Section>
      <Section title="安全边界">
        <Row label="Live trading" value="disabled / endpoint absent" />
        <Row label="Live trade guard" value="PASS · expected rejection (exit 3)" />
        <Row label="生产晋升" value="API unavailable" />
        <Row label="任意Shell" value="API unavailable" />
      </Section>
      <Section title="研究版本策略">
        {versioning.data.available ? (
          <>
            <Row label="权威状态" value="Local authoritative" />
            <Row label="Git角色" value="Manual backup / release" />
            <Row label="Remote依赖" value={versioning.data.remote_required ? "required" : "not required"} />
            <Row label="Intake自动提交" value="disabled" />
            <Row label="Freeze自动提交" value="disabled" />
            <Row label="自动Push" value="disabled" />
            <p className="mt-3 text-xs leading-5 text-slate-500">
              SQLite、项目相对 Artifact、checksum 与 append-only audit 才是研究事实源；冻结 Baseline 不等于 Git commit。
            </p>
          </>
        ) : (
          <p className="text-sm text-amber-200/80">版本策略不可用：{versioning.data.reason ?? "unknown"}</p>
        )}
      </Section>
      <Section title="Worker 资源预算">
        {workerPolicy.data.available ? (
          <>
            <Row label="Policy" value={workerPolicy.data.policy_id ?? "unknown"} />
            <Row label="Max RSS" value={`${workerPolicy.data.max_rss_mb} MiB`} />
            <Row label="Max job" value={`${workerPolicy.data.max_job_minutes} min`} />
            <Row
              label="Concurrent trials"
              value={String(workerPolicy.data.max_concurrent_trials)}
            />
            <Row
              label="Parquet batch"
              value={`${workerPolicy.data.parquet_batch_rows} rows`}
            />
            <Row
              label="Memory limit"
              value={workerPolicy.data.kill_on_memory_limit ? "hard fail + preserve evidence" : "report only"}
            />
          </>
        ) : (
          <p className="text-sm text-amber-200/80">
            Worker 资源策略不可用：{workerPolicy.data.reason ?? "unknown"}
          </p>
        )}
      </Section>
      <Section title="保守交易执行模型 v1">
        {executionModels.data.map((model) => (
          <div key={`${model.model_id}:${model.venue}`} className="space-y-3 border-b border-white/[0.08] pb-4 last:border-0 last:pb-0">
            <Row label="Venue / Symbol" value={`${model.venue} · ${model.symbol}`} />
            <Row label="默认杠杆" value={`${model.default_leverage}x`} />
            <Row label="研究硬上限" value={`${model.max_research_leverage}x · 必须显式设置`} />
            <Row label="保证金" value={`${model.margin_mode} · mark price 强平`} />
            <Row label="同K线顺序" value={model.same_bar_priority.join(" → ")} />
            <Row label="Funding缺口" value={model.funding_policy} />
            <Row
              label="历史杠杆阶梯"
              value={model.historical_tiers_complete ? "complete" : "保守假设 / 未完成"}
            />
            <p className="text-xs leading-5 text-amber-200/80">
              研究模型，不授权交易；50x 安全性不会由模型自动声明。
            </p>
          </div>
        ))}
      </Section>
      <Section title="未来模式（未实现）">
        <Row label="embedded_cloud_provider" value="planned / unsupported" />
        <Row label="byok_provider" value="planned / unsupported" />
        <Row label="local_model_provider" value="planned / unsupported" />
        <Row label="hosted_sandbox" value="planned / unsupported" />
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.025] p-5">
      <h2 className="mb-4 font-medium">{title}</h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-white/[0.06] pb-3 text-sm last:border-0 last:pb-0">
      <span className="text-slate-500">{label}</span>
      <span className="text-right text-slate-200">{value}</span>
    </div>
  );
}
