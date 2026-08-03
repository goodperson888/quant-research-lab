"use client";

import { useQuery } from "@tanstack/react-query";

import { AgentStatus, apiFetch, ProjectStatus } from "@/lib/api";

export function ProjectStatusStrip() {
  const project = useQuery({
    queryKey: ["project-status"],
    queryFn: () => apiFetch<ProjectStatus>("/api/project/status"),
  });
  const agent = useQuery({
    queryKey: ["agent-status"],
    queryFn: () => apiFetch<AgentStatus>("/api/agent/status"),
  });

  if (project.isError || agent.isError) {
    return (
      <div className="rounded-xl border border-rose-300/20 bg-rose-300/5 px-4 py-3 text-sm text-rose-100">
        API 未连接。请先运行 <code>./scripts/dev-api.sh</code>。
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-2 text-xs">
      <StatusPill
        label="API"
        value={project.isPending ? "连接中" : "本机运行正常"}
        active={!project.isPending}
      />
      <StatusPill
        label="当前入口"
        value={entryLabel(agent.data)}
        active={Boolean(agent.data)}
      />
      <StatusPill
        label="执行位置"
        value={cnRuntimeValue(agent.data?.active_configuration.execution_target)}
        active={Boolean(agent.data)}
      />
      <StatusPill
        label="网页发起研究"
        value={webResearchLabel(agent.data)}
        active={Boolean(
          agent.data?.local_connector.available ||
            agent.data?.embedded_provider.configured,
        )}
      />
    </div>
  );
}

function entryLabel(agent: AgentStatus | undefined) {
  if (!agent) return "读取中";
  if (agent.local_connector.available) return "网页调用本地助手";
  if (agent.embedded_provider.configured) return "网页模型 / API Key";
  return "Codex / CLI 直接研究";
}

function webResearchLabel(agent: AgentStatus | undefined) {
  if (!agent) return "读取中";
  if (agent.local_connector.available) return "本地助手已连接";
  if (agent.embedded_provider.configured) return "网页模型已配置";
  return "尚未配置";
}

function cnRuntimeValue(value: string | undefined) {
  if (!value) return "读取中";
  return (
    {
      external_local_agent: "本地研究助手",
      local_runtime: "本机执行",
      embedded_cloud_provider: "网页内云端模型",
    }[value] ?? value
  );
}

function StatusPill({
  label,
  value,
  active,
}: {
  label: string;
  value: string;
  active: boolean;
}) {
  return (
    <div className="rounded-full border border-white/10 bg-white/[0.035] px-3 py-1.5 text-slate-400">
      {label}: <span className={active ? "text-emerald-200" : "text-amber-200"}>{value}</span>
    </div>
  );
}
