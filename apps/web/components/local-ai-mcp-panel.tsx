"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import {
  apiFetch,
  McpConnectionInfo,
} from "@/lib/api";

type CopiedField = "codex" | "verify" | "claude" | null;
type CopyErrorField = Exclude<CopiedField, null> | null;

export function LocalAiMcpPanel() {
  const [copied, setCopied] = useState<CopiedField>(null);
  const [copyError, setCopyError] = useState<CopyErrorField>(null);
  const connection = useQuery({
    queryKey: ["mcp-connection"],
    queryFn: () =>
      apiFetch<McpConnectionInfo>("/api/agent/mcp-connection"),
  });

  async function copy(field: Exclude<CopiedField, null>, value: string) {
    setCopyError(null);
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else if (!copyWithLegacyFallback(value)) {
        throw new Error("clipboard unavailable");
      }
      setCopied(field);
      window.setTimeout(() => setCopied(null), 1800);
    } catch {
      if (copyWithLegacyFallback(value)) {
        setCopied(field);
        window.setTimeout(() => setCopied(null), 1800);
        return;
      }
      setCopyError(field);
    }
  }

  if (connection.isPending) {
    return (
      <section className="rounded-2xl border border-white/10 bg-white/[0.025] p-5 text-sm text-slate-500">
        正在生成本机 AI 连接配置……
      </section>
    );
  }
  if (connection.isError) {
    return (
      <section className="rounded-2xl border border-rose-300/20 bg-rose-300/[0.04] p-5 text-sm text-rose-200">
        无法读取 MCP 连接配置，请确认本地 API 已启动。
      </section>
    );
  }

  const current = connection.data;
  const claudeConfig = JSON.stringify(current.claude.config, null, 2);

  return (
    <section className="rounded-2xl border border-violet-300/20 bg-violet-300/[0.035] p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-slate-500">
            本地 AI 直连
          </p>
          <h2 className="mt-2 text-lg font-medium text-slate-100">
            在 Codex 或 Claude 中直接调用 Quant Lab
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            添加一次 MCP 配置后，可以直接在本地 AI 对话中保存策略、提交结构化提案、
            冻结 Baseline，并在明确授权后启动有边界的研究任务。网页会同步显示同一份结果。
          </p>
        </div>
        <span
          className={`rounded-full border px-3 py-1 text-xs ${
            current.available
              ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100"
              : "border-amber-300/20 bg-amber-300/10 text-amber-100"
          }`}
        >
          {current.available ? "MCP 运行器可用" : "MCP 运行器不可用"}
        </span>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <ConnectionCard
          step="推荐"
          title="连接 Codex"
          description="在终端执行一次安装命令，然后重启 Codex。不会连接或读取其他聊天窗口。"
        >
          <CodeBlock value={current.codex.install_command} />
          <div className="mt-3 flex flex-wrap gap-2">
            <CopyButton
              label={
                copied === "codex"
                  ? "安装命令已复制"
                  : copyError === "codex"
                    ? "复制失败，请手动选择"
                    : "复制安装命令"
              }
              onClick={() =>
                copy("codex", current.codex.install_command)
              }
            />
            <CopyButton
              label={
                copied === "verify"
                  ? "检查命令已复制"
                  : copyError === "verify"
                    ? "复制失败，请手动选择"
                    : "复制检查命令"
              }
              secondary
              onClick={() =>
                copy("verify", current.codex.verify_command)
              }
            />
          </div>
        </ConnectionCard>

        <ConnectionCard
          step="兼容"
          title="连接 Claude 等 MCP 客户端"
          description="把下面配置加入该客户端的 MCP 配置文件。不同客户端的配置入口可能不同。"
        >
          <CodeBlock value={claudeConfig} />
          <div className="mt-3">
            <CopyButton
              label={
                copied === "claude"
                  ? "配置已复制"
                  : copyError === "claude"
                    ? "复制失败，请手动选择"
                    : "复制 MCP 配置"
              }
              onClick={() => copy("claude", claudeConfig)}
            />
          </div>
        </ConnectionCard>
      </div>

      <div className="mt-5 grid gap-4 border-t border-white/[0.08] pt-5 lg:grid-cols-[1fr_1.2fr]">
        <div>
          <h3 className="text-sm font-medium text-slate-200">首次连接步骤</h3>
          <ol className="mt-3 space-y-2 text-xs leading-5 text-slate-400">
            {current.requirements.map((requirement, index) => (
              <li key={requirement} className="flex gap-3">
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-white/[0.06] text-[11px] text-slate-300">
                  {index + 1}
                </span>
                <span className="pt-0.5">{requirement}</span>
              </li>
            ))}
          </ol>
        </div>
        <div className="rounded-xl border border-white/[0.08] bg-black/10 p-4">
          <h3 className="text-sm font-medium text-slate-200">隐私与权限</h3>
          <p className="mt-2 text-xs leading-5 text-slate-400">
            {current.privacy_note}
          </p>
          <p className="mt-3 text-xs leading-5 text-amber-200/80">
            AI 不能替用户确认策略、冻结 Baseline、扩大预算或启动实盘。
            每个关键动作仍由本地授权、状态机和审计记录校验。
          </p>
        </div>
      </div>
    </section>
  );
}

function copyWithLegacyFallback(value: string): boolean {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  return copied;
}

function ConnectionCard({
  step,
  title,
  description,
  children,
}: {
  step: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/10 p-4">
      <div className="flex items-center gap-3">
        <span className="rounded-full bg-violet-300/10 px-2.5 py-1 text-[11px] text-violet-100">
          {step}
        </span>
        <h3 className="text-sm font-medium text-slate-100">{title}</h3>
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-400">{description}</p>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function CodeBlock({ value }: { value: string }) {
  return (
    <pre className="max-h-52 overflow-auto rounded-lg border border-white/[0.08] bg-[#050a0f] p-3 text-xs leading-5 text-sky-100">
      <code>{value}</code>
    </pre>
  );
}

function CopyButton({
  label,
  onClick,
  secondary = false,
}: {
  label: string;
  onClick: () => void;
  secondary?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`min-h-11 rounded-lg px-4 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 ${
        secondary
          ? "border border-white/10 text-slate-200 hover:bg-white/[0.05]"
          : "bg-violet-300/15 text-violet-100 hover:bg-violet-300/20"
      }`}
    >
      {label}
    </button>
  );
}
