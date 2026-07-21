"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { z } from "zod";

import {
  AgentStatus,
  apiFetch,
  AuditEvent,
  ProjectStatus,
  Session,
  StrategyDraft,
} from "@/lib/api";

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

export function StudioWorkspace() {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("ETH 永续策略研究");
  const [sourceType, setSourceType] = useState<"natural_language" | "pine" | "file">(
    "natural_language",
  );
  const [content, setContent] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [draft, setDraft] = useState<StrategyDraft | null>(null);
  const [baseline, setBaseline] = useState<BaselineVersion | null>(null);
  const [notice, setNotice] = useState("等待输入第一份真实策略。AI Provider 当前未配置。");

  const project = useQuery({
    queryKey: ["project-status"],
    queryFn: () => apiFetch<ProjectStatus>("/api/project/status"),
  });
  const agent = useQuery({
    queryKey: ["agent-status"],
    queryFn: () => apiFetch<AgentStatus>("/api/agent/status"),
  });
  const events = useQuery({
    queryKey: ["audit-events"],
    queryFn: () => apiFetch<AuditEvent[]>("/api/audit/events?limit=20"),
  });

  const saveIntake = useMutation({
    mutationFn: async () => {
      const values = intakeSchema.parse({ title, sourceType, content });
      const activeSession =
        session ??
        (await apiFetch<Session>("/api/research/sessions", {
          method: "POST",
          body: JSON.stringify({ title: values.title }),
        }));
      if (!session) setSession(activeSession);
      await apiFetch(`/api/research/sessions/${activeSession.id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: values.content }),
      });
      return apiFetch<StrategyDraft>(
        `/api/research/sessions/${activeSession.id}/intakes`,
        {
          method: "POST",
          body: JSON.stringify({
            source_type: values.sourceType,
            source_name: values.sourceType === "file" ? "file-intake-placeholder" : null,
            raw_content: values.content,
          }),
        },
      );
    },
    onSuccess: (created) => {
      setDraft(created);
      setBaseline(null);
      setNotice(
        "原始来源已保存为 draft。未运行AI形式化；请先审阅原文和歧义，再人工冻结 baseline。",
      );
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });

  const freeze = useMutation({
    mutationFn: () =>
      apiFetch<BaselineVersion>(`/api/strategy-drafts/${draft?.id}/freeze-baseline`, {
        method: "POST",
        body: JSON.stringify({ confirmed_by_user: true }),
      }),
    onSuccess: (version) => {
      setBaseline(version);
      setDraft((current) =>
        current
          ? { ...current, status: "baseline_frozen", baseline_version_id: version.id }
          : current,
      );
      setNotice("baseline v0 已冻结且不可覆盖。后续修改必须创建 Proposal/新版本。");
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });

  const formError = useMemo(() => {
    const error = saveIntake.error ?? freeze.error;
    if (error instanceof z.ZodError) return error.issues[0]?.message;
    return error instanceof Error ? error.message : null;
  }, [freeze.error, saveIntake.error]);

  return (
    <div className="min-h-screen p-4 md:p-6">
      <header className="mb-5 flex flex-col gap-4 border-b border-white/10 pb-5 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-300">
            AI Strategy Research Studio
          </div>
          <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">
            对话提出想法，结构化成果接受审计
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            External Agent 是阶段0优先执行者；Web展示相同的计划、工具调用、审批和成果记录，不隐藏AI动作。
          </p>
        </div>
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <Status label="Agent Provider" value="external_local_agent" tone="green" />
          <Status label="Execution Target" value="local_runtime" tone="green" />
          <Status
            label="Local Agent"
            value={agent.data?.external_agent.connection_status ?? "读取中"}
            tone="amber"
          />
          <Status
            label="Embedded Provider"
            value={project.data?.ai_provider.configured ? "configured" : "未配置"}
            tone="amber"
          />
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)_360px]">
        <aside className="space-y-4">
          <Panel title="Research Session">
            <div className="space-y-2 text-sm">
              <Meta label="会话" value={session?.title ?? "尚未创建"} />
              <Meta label="状态" value={session?.status ?? "inbox"} />
              <Meta label="策略" value={draft ? "1 draft" : "0"} />
            </div>
          </Panel>
          <Panel title="Agent Runs">
            <div className="rounded-lg border border-dashed border-white/15 p-3 text-xs leading-5 text-slate-400">
              尚无 AgentRun。默认模式为 <span className="text-slate-200">guided</span>；读取和安全研究任务可自动，冻结与参数搜索必须审批。
            </div>
          </Panel>
          <Panel title="Audit Timeline">
            <div className="max-h-72 space-y-3 overflow-auto pr-1">
              {events.data?.length ? (
                events.data.map((event) => (
                  <div key={event.id} className="border-l border-emerald-300/25 pl-3 text-xs">
                    <div className="text-slate-200">{event.event_type}</div>
                    <div className="mt-1 text-slate-500">{event.actor_type}</div>
                  </div>
                ))
              ) : (
                <div className="text-xs text-slate-500">关键动作将在这里按追加顺序出现。</div>
              )}
            </div>
          </Panel>
        </aside>

        <section className="flex min-h-[680px] flex-col rounded-2xl border border-white/10 bg-[#0a151e]/90">
          <div className="border-b border-white/10 px-5 py-4">
            <div className="text-sm font-semibold">策略对话与原始输入</div>
            <div className="mt-1 text-xs text-slate-500">消息是入口，结构化状态和审计事件才是权威记录。</div>
          </div>
          <div className="flex-1 space-y-4 p-5">
            <div className="max-w-[88%] rounded-2xl rounded-tl-sm border border-emerald-300/15 bg-emerald-300/[0.06] p-4 text-sm leading-6 text-slate-300">
              描述策略规则、粘贴 Pine Script，或预留文件来源。阶段0会保存原始内容，不会伪造AI分析结果。
            </div>
            {draft ? (
              <div className="ml-auto max-w-[88%] rounded-2xl rounded-tr-sm bg-slate-100 p-4 text-sm leading-6 text-slate-900">
                {draft.raw_content}
              </div>
            ) : null}
            <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3 text-xs leading-5 text-slate-400">
              {notice}
            </div>
          </div>
          <form
            className="border-t border-white/10 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              saveIntake.mutate();
            }}
          >
            <div className="mb-3 grid gap-3 sm:grid-cols-[1fr_180px]">
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                className="rounded-xl border border-white/10 bg-[#071017] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600"
                placeholder="研究会话标题"
              />
              <select
                value={sourceType}
                onChange={(event) => setSourceType(event.target.value as typeof sourceType)}
                className="rounded-xl border border-white/10 bg-[#071017] px-3 py-2.5 text-sm text-slate-200"
              >
                <option value="natural_language">自然语言</option>
                <option value="pine">Pine Script</option>
                <option value="file">文件来源占位</option>
              </select>
            </div>
            <textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              className="min-h-32 w-full resize-y rounded-xl border border-white/10 bg-[#071017] p-3 text-sm leading-6 text-slate-100 placeholder:text-slate-600"
              placeholder="输入原始策略，不要先为了盈利而改写……"
            />
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <div className="text-xs text-slate-500">
                文件上传接口尚未启用；不接受任意路径。
              </div>
              <button
                type="submit"
                disabled={saveIntake.isPending}
                className="rounded-xl bg-emerald-300 px-4 py-2.5 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {saveIntake.isPending ? "保存中…" : "保存为策略 Draft"}
              </button>
            </div>
            {formError ? <div className="mt-3 text-sm text-rose-300">{formError}</div> : null}
          </form>
        </section>

        <aside className="space-y-4">
          <Panel title="结构化策略">
            <div className="space-y-2 text-sm">
              <Meta label="Draft" value={draft?.id.slice(0, 18) ?? "未创建"} />
              <Meta label="来源" value={draft?.source_type ?? "—"} />
              <Meta label="状态" value={draft?.status ?? "—"} />
              <Meta label="AI形式化" value="未运行" />
            </div>
          </Panel>
          <Panel title="歧义与 Proposal">
            <div className="rounded-lg border border-amber-300/15 bg-amber-300/[0.05] p-3 text-xs leading-5 text-amber-100/80">
              Embedded Provider 未配置，因此不生成假歧义或假建议。后续 External Agent 的输出必须作为 draft/proposal 写入控制平面。
            </div>
          </Panel>
          <Panel title="Experiment Plan">
            <ul className="space-y-2 text-xs text-slate-400">
              <li>• 单一研究假设：待定义</li>
              <li>• 参数范围 / 目标 / 约束：待定义</li>
              <li>• Train / Validation / Locked test：待定义</li>
              <li>• Trials / 时间预算 / 停止条件：待审批</li>
            </ul>
          </Panel>
          <Panel title="审批与成果">
            <div className="mb-3 text-xs leading-5 text-slate-400">
              baseline 冻结后不可覆盖；策略 diff、实验、Trial 与报告将作为独立 Artifact 展示。
            </div>
            <button
              type="button"
              disabled={!draft || Boolean(draft.baseline_version_id) || freeze.isPending}
              onClick={() => freeze.mutate()}
              className="w-full rounded-xl border border-emerald-300/30 bg-emerald-300/10 px-3 py-2.5 text-sm font-medium text-emerald-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {baseline ? "baseline v0 已冻结" : "人工确认并冻结 baseline v0"}
            </button>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-[#0a151e]/90 p-4">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{title}</h2>
      {children}
    </section>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-white/[0.06] pb-2 last:border-0 last:pb-0">
      <span className="text-slate-500">{label}</span>
      <span className="max-w-[65%] break-all text-right text-slate-200">{value}</span>
    </div>
  );
}

function Status({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "green" | "amber";
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2">
      <div className="text-slate-500">{label}</div>
      <div className={tone === "green" ? "mt-1 text-emerald-200" : "mt-1 text-amber-200"}>
        {value}
      </div>
    </div>
  );
}
