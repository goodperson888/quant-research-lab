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
        value={project.isPending ? "连接中" : "local / healthy"}
        active={!project.isPending}
      />
      <StatusPill
        label="Agent Provider"
        value={agent.data?.active_configuration.agent_provider ?? "读取中"}
        active={Boolean(agent.data)}
      />
      <StatusPill
        label="Execution"
        value={agent.data?.active_configuration.execution_target ?? "读取中"}
        active={Boolean(agent.data)}
      />
      <StatusPill
        label="Embedded AI"
        value={project.data?.ai_provider.configured ? "configured" : "未配置"}
        active={false}
      />
    </div>
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
