"use client";

import {
  AgentStatus,
  AssistantEntryMode,
  ProviderStatus,
} from "@/lib/api";

const OPTIONS: Array<{
  mode: AssistantEntryMode;
  title: string;
  description: string;
}> = [
  {
    mode: "external_agent_direct",
    title: "直接在 Codex 研究",
    description:
      "在当前 Codex 对话里描述和确认策略；网页只负责展示会话、审批、任务和结果。",
  },
  {
    mode: "web_local_connector",
    title: "网页调用本地助手",
    description:
      "在网页提交策略，本地连接器自动交给 Codex CLI 形式化，再把结果写回当前会话。",
  },
  {
    mode: "web_provider",
    title: "网页模型 / API Key",
    description:
      "在网页提交策略，由当前 API 进程内存中的模型配置生成结构化提案。",
  },
];

export function AssistantEntry({
  mode,
  agentStatus,
  providerStatus,
  providerName,
  baseUrl,
  model,
  apiKey,
  providerPending,
  providerError,
  onModeChange,
  onProviderNameChange,
  onBaseUrlChange,
  onModelChange,
  onApiKeyChange,
  onConfigureProvider,
  onClearProvider,
}: {
  mode: AssistantEntryMode;
  agentStatus: AgentStatus | undefined;
  providerStatus: ProviderStatus | undefined;
  providerName: string;
  baseUrl: string;
  model: string;
  apiKey: string;
  providerPending: boolean;
  providerError: string | null;
  onModeChange: (mode: AssistantEntryMode) => void;
  onProviderNameChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onConfigureProvider: () => void;
  onClearProvider: () => void;
}) {
  const connector = agentStatus?.local_connector;
  const providerConfigured = Boolean(providerStatus?.configured);

  return (
    <section
      aria-labelledby="assistant-entry-title"
      className="rounded-2xl border border-white/10 bg-black/10 p-4"
    >
      <div>
        <h2
          id="assistant-entry-title"
          className="text-sm font-semibold text-slate-100"
        >
          选择本次策略由谁开始处理
        </h2>
        <p className="mt-1 text-xs leading-5 text-slate-400">
          三种入口共用同一套研究会话和结果，但一次只启用一种执行链。
        </p>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-3" role="radiogroup">
        {OPTIONS.map((option) => {
          const selected = option.mode === mode;
          const status = entryStatus(option.mode, agentStatus, providerStatus);
          return (
            <button
              key={option.mode}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onModeChange(option.mode)}
              className={`cursor-pointer rounded-xl border p-4 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300 ${
                selected
                  ? "border-emerald-300/45 bg-emerald-300/[0.09]"
                  : "border-white/[0.08] bg-white/[0.025] hover:border-white/20 hover:bg-white/[0.045]"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <span className="text-sm font-medium text-slate-100">
                  {option.title}
                </span>
                <span
                  className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] ${
                    status.available
                      ? "bg-emerald-300/10 text-emerald-100"
                      : "bg-amber-300/10 text-amber-100"
                  }`}
                >
                  {status.label}
                </span>
              </div>
              <p className="mt-3 text-xs leading-5 text-slate-400">
                {option.description}
              </p>
            </button>
          );
        })}
      </div>

      {mode === "external_agent_direct" ? (
        <div className="mt-4 rounded-xl border border-sky-300/20 bg-sky-300/[0.06] px-4 py-3 text-sm leading-6 text-sky-100">
          现在直接回到 Codex 描述策略即可。此模式不会显示网页策略输入框，也不会自动启动第二个 AI。
        </div>
      ) : null}

      {mode === "web_local_connector" ? (
        <div
          className={`mt-4 rounded-xl border px-4 py-3 text-sm leading-6 ${
            connector?.available
              ? "border-emerald-300/20 bg-emerald-300/[0.06] text-emerald-100"
              : "border-amber-300/20 bg-amber-300/[0.06] text-amber-100"
          }`}
        >
          {connector?.note ?? "正在读取 Local Connector 状态…"}
          {connector?.state === "running" ? " 当前正在处理一个策略形式化任务。" : ""}
        </div>
      ) : null}

      {mode === "web_provider" ? (
        <section
          aria-labelledby="provider-config-title"
          className="mt-4 rounded-xl border border-white/10 bg-[#071017] p-4"
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3
                id="provider-config-title"
                className="text-sm font-semibold text-slate-100"
              >
                网页模型配置
              </h3>
              <p className="mt-1 text-xs leading-5 text-slate-400">
                Key 只存在 API 进程内存，不写浏览器存储、SQLite、配置文件或日志；API
                重启后自动清空。
              </p>
            </div>
            <span
              className={`w-fit rounded-full px-2.5 py-1 text-[11px] ${
                providerConfigured
                  ? "bg-emerald-300/10 text-emerald-100"
                  : "bg-white/[0.06] text-slate-300"
              }`}
            >
              {providerConfigured
                ? `${providerStatus?.provider} · ${providerStatus?.model}`
                : "尚未配置"}
            </span>
          </div>

          {!providerConfigured ? (
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <ProviderField
                id="provider-name"
                label="服务商名称"
                value={providerName}
                placeholder="例如 OpenAI"
                onChange={onProviderNameChange}
              />
              <ProviderField
                id="provider-model"
                label="模型 ID"
                value={model}
                placeholder="填写服务商提供的模型 ID"
                onChange={onModelChange}
              />
              <div className="sm:col-span-2">
                <ProviderField
                  id="provider-base-url"
                  label="OpenAI-compatible Base URL"
                  value={baseUrl}
                  placeholder="https://api.openai.com/v1"
                  onChange={onBaseUrlChange}
                />
              </div>
              <div className="sm:col-span-2">
                <ProviderField
                  id="provider-api-key"
                  label="API Key"
                  value={apiKey}
                  type="password"
                  autoComplete="off"
                  placeholder="仅发送到本机 API 进程内存"
                  onChange={onApiKeyChange}
                />
              </div>
              {providerError ? (
                <p
                  role="alert"
                  className="sm:col-span-2 rounded-lg border border-rose-300/20 bg-rose-300/[0.06] px-3 py-2 text-xs leading-5 text-rose-100"
                >
                  {providerError}
                </p>
              ) : null}
              <button
                type="button"
                disabled={providerPending}
                onClick={onConfigureProvider}
                className="min-h-11 cursor-pointer rounded-xl bg-emerald-300 px-4 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-50 sm:col-span-2"
              >
                {providerPending ? "正在安全配置…" : "只在本次 API 进程中启用"}
              </button>
            </div>
          ) : (
            <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs leading-5 text-slate-400">
                {providerStatus?.message}
              </p>
              <button
                type="button"
                onClick={onClearProvider}
                className="min-h-11 shrink-0 cursor-pointer rounded-xl border border-white/15 px-4 text-sm text-slate-200 transition hover:bg-white/[0.05]"
              >
                清除内存配置
              </button>
            </div>
          )}
        </section>
      ) : null}
    </section>
  );
}

function entryStatus(
  mode: AssistantEntryMode,
  agentStatus: AgentStatus | undefined,
  providerStatus: ProviderStatus | undefined,
) {
  if (!agentStatus) return { available: false, label: "读取中" };
  if (mode === "external_agent_direct") {
    return { available: true, label: "可用" };
  }
  if (mode === "web_local_connector") {
    return {
      available: agentStatus.local_connector.available,
      label: agentStatus.local_connector.available ? "已连接" : "未启动",
    };
  }
  return {
    available: Boolean(providerStatus?.configured),
    label: providerStatus?.configured ? "已配置" : "需配置",
  };
}

function ProviderField({
  id,
  label,
  value,
  placeholder,
  type = "text",
  autoComplete,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  placeholder: string;
  type?: string;
  autoComplete?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label htmlFor={id} className="block text-xs font-medium text-slate-300">
      {label}
      <input
        id={id}
        type={type}
        value={value}
        autoComplete={autoComplete}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 min-h-11 w-full rounded-xl border border-white/10 bg-black/20 px-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-emerald-300/50 focus:ring-2 focus:ring-emerald-300/10"
        placeholder={placeholder}
      />
    </label>
  );
}
