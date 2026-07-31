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
        label="研究助手"
        value={cnRuntimeValue(agent.data?.active_configuration.agent_provider)}
        active={Boolean(agent.data)}
      />
      <StatusPill
        label="执行位置"
        value={cnRuntimeValue(agent.data?.active_configuration.execution_target)}
        active={Boolean(agent.data)}
      />
      <StatusPill
        label="网页内 AI"
        value={project.data?.ai_provider.configured ? "已配置" : "未配置"}
        active={false}
      />
    </div>
  );
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
