"use client";

import { useQuery } from "@tanstack/react-query";

import { AgentManifest, AgentStatus, apiFetch, ProjectStatus } from "@/lib/api";

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
  if (project.isPending || agent.isPending || manifest.isPending)
    return <div className="text-sm text-slate-500">读取本地设置…</div>;
  if (project.isError || agent.isError || manifest.isError)
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
        <Row label="生产晋升" value="API unavailable" />
        <Row label="任意Shell" value="API unavailable" />
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
