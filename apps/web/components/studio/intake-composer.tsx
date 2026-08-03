"use client";

import {
  AgentRun,
  AgentStatus,
  AssistantEntryMode,
  ProviderStatus,
} from "@/lib/api";
import { AssistantEntry } from "@/components/studio/assistant-entry";

export function IntakeComposer({
  title,
  sourceType,
  content,
  latestAgentRun,
  assistantMode,
  agentStatus,
  providerStatus,
  providerName,
  providerBaseUrl,
  providerModel,
  providerApiKey,
  providerPending,
  providerError,
  notice,
  pending,
  error,
  onAssistantModeChange,
  onProviderNameChange,
  onProviderBaseUrlChange,
  onProviderModelChange,
  onProviderApiKeyChange,
  onConfigureProvider,
  onClearProvider,
  onTitleChange,
  onSourceTypeChange,
  onContentChange,
  onSubmit,
}: {
  title: string;
  sourceType: "natural_language" | "pine" | "file";
  content: string;
  latestAgentRun: AgentRun | null;
  assistantMode: AssistantEntryMode;
  agentStatus: AgentStatus | undefined;
  providerStatus: ProviderStatus | undefined;
  providerName: string;
  providerBaseUrl: string;
  providerModel: string;
  providerApiKey: string;
  providerPending: boolean;
  providerError: string | null;
  notice: string;
  pending: boolean;
  error: string | null;
  onAssistantModeChange: (mode: AssistantEntryMode) => void;
  onProviderNameChange: (value: string) => void;
  onProviderBaseUrlChange: (value: string) => void;
  onProviderModelChange: (value: string) => void;
  onProviderApiKeyChange: (value: string) => void;
  onConfigureProvider: () => void;
  onClearProvider: () => void;
  onTitleChange: (value: string) => void;
  onSourceTypeChange: (value: "natural_language" | "pine" | "file") => void;
  onContentChange: (value: string) => void;
  onSubmit: () => void;
}) {
  const canSubmit =
    assistantMode === "web_local_connector"
      ? Boolean(agentStatus?.local_connector.available)
      : assistantMode === "web_provider"
        ? Boolean(providerStatus?.configured)
        : false;

  return (
    <section className="order-2 flex min-w-0 flex-col rounded-2xl border border-white/10 bg-[#0a151e]/90 xl:order-1 xl:col-start-1">
      <div className="border-b border-white/10 px-5 py-4">
        <div className="text-sm font-semibold">策略研究入口</div>
        <div className="mt-1 text-xs leading-5 text-slate-500">
          先选执行入口；页面只展示当前模式需要的操作和真实运行状态。
        </div>
      </div>

      <div className="flex-1 space-y-4 p-5">
        <AssistantEntry
          mode={assistantMode}
          agentStatus={agentStatus}
          providerStatus={providerStatus}
          providerName={providerName}
          baseUrl={providerBaseUrl}
          model={providerModel}
          apiKey={providerApiKey}
          providerPending={providerPending}
          providerError={providerError}
          onModeChange={onAssistantModeChange}
          onProviderNameChange={onProviderNameChange}
          onBaseUrlChange={onProviderBaseUrlChange}
          onModelChange={onProviderModelChange}
          onApiKeyChange={onProviderApiKeyChange}
          onConfigureProvider={onConfigureProvider}
          onClearProvider={onClearProvider}
        />

        {latestAgentRun ? <AgentRunState run={latestAgentRun} /> : null}

        <div aria-live="polite" className="sr-only">
          {notice}
        </div>
      </div>

      {assistantMode !== "external_agent_direct" ? (
        <form
          className="border-t border-white/10 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          <div className="mb-4">
            <h2 className="text-sm font-semibold text-slate-100">
              输入策略并启动形式化
            </h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              本步只保存原文并生成待确认的结构化提案，不会自动冻结基准、调参或回测。
            </p>
          </div>
          <div className="mb-3 grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_180px]">
            <div>
              <label
                htmlFor="strategy-session-title"
                className="mb-2 block text-xs font-medium text-slate-300"
              >
                研究会话标题
              </label>
              <input
                id="strategy-session-title"
                value={title}
                onChange={(event) => onTitleChange(event.target.value)}
                className="min-h-11 w-full min-w-0 rounded-xl border border-white/10 bg-[#071017] px-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-emerald-300/50 focus:ring-2 focus:ring-emerald-300/10"
                placeholder="例如：ETH 回踩趋势策略"
              />
            </div>
            <div>
              <label
                htmlFor="strategy-source-type"
                className="mb-2 block text-xs font-medium text-slate-300"
              >
                原始内容类型
              </label>
              <select
                id="strategy-source-type"
                value={sourceType}
                onChange={(event) =>
                  onSourceTypeChange(
                    event.target.value as "natural_language" | "pine" | "file",
                  )
                }
                className="min-h-11 w-full min-w-0 rounded-xl border border-white/10 bg-[#071017] px-3 text-sm text-slate-200 outline-none focus:border-emerald-300/50 focus:ring-2 focus:ring-emerald-300/10"
              >
                <option value="natural_language">自然语言</option>
                <option value="pine">Pine Script</option>
                <option value="file" disabled>
                  文件（尚未启用）
                </option>
              </select>
            </div>
          </div>
          <label
            htmlFor="strategy-raw-content"
            className="mb-2 block text-xs font-medium text-slate-300"
          >
            策略原文或 Pine Script
          </label>
          <textarea
            id="strategy-raw-content"
            value={content}
            onChange={(event) => onContentChange(event.target.value)}
            className="min-h-32 w-full resize-y rounded-xl border border-white/10 bg-[#071017] p-3 text-base leading-6 text-slate-100 outline-none placeholder:text-slate-600 focus:border-emerald-300/50 focus:ring-2 focus:ring-emerald-300/10 sm:text-sm"
            placeholder="例如：ETH 5分钟，当15分钟趋势向上且价格回踩均线后重新站上时做多；止损放在回调低点下方……也可以直接粘贴 Pine Script。"
          />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs leading-5 text-slate-500">
              {canSubmit
                ? "提交后会立即创建 AgentRun，可在上方看到排队、运行和待确认状态。"
                : assistantMode === "web_local_connector"
                  ? "Local Connector 未运行，暂不能提交。"
                  : "请先完成网页模型配置。"}
            </div>
            <button
              type="submit"
              disabled={pending || !canSubmit}
              className="min-h-11 cursor-pointer rounded-xl bg-emerald-300 px-4 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {pending
                ? "正在创建 AI 任务…"
                : assistantMode === "web_local_connector"
                  ? "交给本地 Codex"
                  : "交给网页模型"}
            </button>
          </div>
          {error ? (
            <div role="alert" className="mt-3 text-sm text-rose-300">
              {error}
            </div>
          ) : null}
        </form>
      ) : null}
    </section>
  );
}

function AgentRunState({ run }: { run: AgentRun }) {
  const presentation = {
    queued: ["已排队", "任务已创建，正在等待执行器领取。", "amber"],
    running: ["正在分析", "AI 正在读取策略并生成结构化提案。", "sky"],
    waiting_approval: [
      "等待你确认",
      "结构化提案已写回，请核对规则与歧义后再确认。",
      "emerald",
    ],
    paused: ["已暂停", "任务已暂停，等待下一步操作。", "amber"],
    completed: ["已完成", "本次 AI 任务已完成。", "emerald"],
    failed: ["执行失败", run.error ?? "执行器没有返回可用结果。", "rose"],
    cancelled: ["已取消", "本次任务已取消，已有记录仍保留。", "slate"],
  }[run.status];
  const tone = presentation[2];
  const classes =
    tone === "emerald"
      ? "border-emerald-300/20 bg-emerald-300/[0.06] text-emerald-100"
      : tone === "sky"
        ? "border-sky-300/20 bg-sky-300/[0.06] text-sky-100"
        : tone === "rose"
          ? "border-rose-300/20 bg-rose-300/[0.06] text-rose-100"
          : "border-amber-300/20 bg-amber-300/[0.06] text-amber-100";
  return (
    <section aria-live="polite" className={`rounded-xl border p-4 ${classes}`}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="text-sm font-semibold">{presentation[0]}</h3>
          <p className="mt-1 text-xs leading-5 opacity-85">{presentation[1]}</p>
        </div>
        <span className="w-fit rounded-full bg-black/15 px-2.5 py-1 text-[11px]">
          {run.agent_name}
        </span>
      </div>
    </section>
  );
}
