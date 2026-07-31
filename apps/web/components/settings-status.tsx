"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  AgentManifest,
  AgentStatus,
  apiFetch,
  ExecutionModelSummary,
  ProjectStatus,
  VersioningPolicy,
  WorkerResourcePolicy,
} from "@/lib/api";

type SettingsView = "common" | "advanced";

export function SettingsStatus() {
  const [view, setView] = useState<SettingsView>("common");
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

  if (
    project.isPending ||
    agent.isPending ||
    manifest.isPending ||
    workerPolicy.isPending ||
    versioning.isPending ||
    executionModels.isPending
  ) {
    return <div className="text-sm text-slate-500">正在读取本地设置……</div>;
  }
  if (
    project.isError ||
    agent.isError ||
    manifest.isError ||
    workerPolicy.isError ||
    versioning.isError ||
    executionModels.isError
  ) {
    return <div className="text-sm text-rose-300">API 未连接，无法读取真实状态。</div>;
  }

  return (
    <div className="space-y-5">
      <div
        className="inline-flex rounded-xl border border-white/10 bg-black/10 p-1"
        role="tablist"
        aria-label="本地设置分类"
      >
        <SettingsTab
          active={view === "common"}
          label="常用状态"
          onClick={() => setView("common")}
        />
        <SettingsTab
          active={view === "advanced"}
          label="高级与技术"
          onClick={() => setView("advanced")}
        />
      </div>

      {view === "common" ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="当前运行方式">
            <Row
              label="研究助手"
              value={cnRuntimeValue(agent.data.active_configuration.agent_provider)}
            />
            <Row
              label="执行位置"
              value={cnRuntimeValue(agent.data.active_configuration.execution_target)}
            />
            <Row
              label="默认运行模式"
              value={cnRuntimeValue(agent.data.default_run_mode)}
            />
            <Row
              label="研究数据位置"
              value={cnRuntimeValue(agent.data.active_configuration.data_location)}
            />
            <p className="text-xs leading-5 text-slate-500">
              当前由本地研究助手理解策略，回测和文件处理在本机执行。
            </p>
          </Section>

          <Section title="网页内 AI">
            <Row
              label="配置状态"
              value={project.data.ai_provider.configured ? "已配置" : "尚未配置"}
            />
            <p className="text-xs leading-5 text-slate-500">
              {project.data.ai_provider.configured
                ? "网页可以直接调用已配置的模型服务。"
                : "当前仍使用本地研究助手；以后配置模型 API 后，可在网页内完成相同研究流程。"}
            </p>
          </Section>

          <Section title="交易安全边界">
            <Row label="实盘交易" value="已禁用，接口不存在" />
            <Row label="安全拦截测试" value="通过：实盘命令按预期被拒绝" />
            <Row label="自动晋升实盘" value="不支持" />
            <Row label="任意系统命令" value="不支持" />
            <p className="text-xs leading-5 text-amber-200/80">
              当前产品只允许历史研究与模拟验证，不会因策略回测通过而自动下单。
            </p>
          </Section>

          <Section title="任务资源预算">
            {workerPolicy.data.available ? (
              <>
                <Row
                  label="单任务最长时间"
                  value={`${workerPolicy.data.max_job_minutes} 分钟`}
                />
                <Row
                  label="最大内存"
                  value={`${workerPolicy.data.max_rss_mb} MiB`}
                />
                <Row
                  label="并行参数方案"
                  value={`${workerPolicy.data.max_concurrent_trials} 个`}
                />
                <Row
                  label="内存超限处理"
                  value={
                    workerPolicy.data.kill_on_memory_limit
                      ? "停止任务并保留已有证据"
                      : "仅记录告警"
                  }
                />
              </>
            ) : (
              <Unavailable reason={workerPolicy.data.reason} />
            )}
          </Section>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="模型能力要求">
            {manifest.data.available && manifest.data.minimum_capabilities ? (
              <>
                <Row
                  label="能力策略"
                  value={cnRuntimeValue(manifest.data.policy_id ?? "未命名")}
                />
                <Row label="原生工具调用" value="必须支持" />
                <Row label="结构化输出" value="必须支持 JSON Schema" />
                <Row label="多轮工具结果" value="必须支持" />
                <Row
                  label="最小上下文"
                  value={`不少于 ${manifest.data.minimum_capabilities.minimum_context_tokens / 1024}K 个上下文词元`}
                />
                <Row
                  label="支持语言"
                  value={manifest.data.minimum_capabilities.languages
                    .map(cnLanguage)
                    .join(" / ")}
                />
                <p className="text-xs leading-5 text-amber-200/80">
                  不使用只能聊天、无法可靠调用工具或输出结构化结果的弱模型替代。
                </p>
              </>
            ) : (
              <Unavailable reason={manifest.data.reason} />
            )}
          </Section>

          <Section title="研究版本与备份">
            {versioning.data.available ? (
              <>
                <Row label="研究事实来源" value="本地数据库与研究产物" />
                <Row label="Git 的作用" value="人工备份与版本发布" />
                <Row
                  label="远程仓库"
                  value={versioning.data.remote_required ? "必须连接" : "日常研究不依赖"}
                />
                <Row label="保存策略时自动提交" value="关闭" />
                <Row label="冻结基准时自动提交" value="关闭" />
                <Row label="自动上传远程仓库" value="关闭" />
                <p className="text-xs leading-5 text-slate-500">
                  策略基准由本地数据库、文件校验值和审计记录保证不可覆盖，不需要每次都提交 Git。
                </p>
              </>
            ) : (
              <Unavailable reason={versioning.data.reason} />
            )}
          </Section>

          <Section title="保守交易执行模型">
            {executionModels.data.map((model) => (
              <div
                key={`${model.model_id}:${model.venue}`}
                className="space-y-3 border-b border-white/[0.08] pb-4 last:border-0 last:pb-0"
              >
                <Row label="交易所与标的" value={`${model.venue} · ${model.symbol}`} />
                <Row label="默认杠杆" value={`${model.default_leverage} 倍`} />
                <Row
                  label="研究杠杆上限"
                  value={`${model.max_research_leverage} 倍，必须明确设置`}
                />
                <Row
                  label="保证金与强平"
                  value={`${cnRuntimeValue(model.margin_mode)} · 按标记价格估算`}
                />
                <Row
                  label="同根 K 线处理顺序"
                  value={model.same_bar_priority.map(cnRuntimeValue).join(" → ")}
                />
                <Row
                  label="资金费率缺口"
                  value={cnRuntimeValue(model.funding_policy)}
                />
                <Row
                  label="历史杠杆阶梯"
                  value={model.historical_tiers_complete ? "完整" : "采用保守假设，尚未完整"}
                />
                <p className="text-xs leading-5 text-amber-200/80">
                  这是研究模型，不构成实盘授权，也不会自动声明高杠杆安全。
                </p>
              </div>
            ))}
          </Section>

          <Section title="未来可扩展方式">
            <Row label="网页托管模型" value="协议已预留，尚未实现" />
            <Row label="用户自带模型密钥" value="协议已预留，尚未实现" />
            <Row label="本地开源模型" value="协议已预留，尚未实现" />
            <Row label="云端隔离执行环境" value="协议已预留，尚未实现" />
          </Section>

          <Section title="任务存储批次">
            {workerPolicy.data.available ? (
              <>
                <Row
                  label="资源策略"
                  value={cnRuntimeValue(workerPolicy.data.policy_id ?? "未命名")}
                />
                <Row
                  label="Parquet 每批行数"
                  value={`${(workerPolicy.data.parquet_batch_rows ?? 0).toLocaleString("zh-CN")} 行`}
                />
              </>
            ) : (
              <Unavailable reason={workerPolicy.data.reason} />
            )}
          </Section>
        </div>
      )}
    </div>
  );
}

function SettingsTab({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={
        active
          ? "min-h-11 rounded-lg bg-sky-300/10 px-4 text-sm text-sky-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
          : "min-h-11 rounded-lg px-4 text-sm text-slate-400 transition hover:bg-white/[0.04] hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
      }
    >
      {label}
    </button>
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

function Unavailable({ reason }: { reason?: string | null }) {
  return (
    <p className="text-sm leading-6 text-amber-200/80">
      当前配置不可用。{reason ? `技术原因：${reason}` : ""}
    </p>
  );
}

function cnLanguage(value: string) {
  return (
    {
      zh: "中文",
      "zh-CN": "中文",
      en: "英文",
    }[value] ?? value
  );
}

function cnRuntimeValue(value: string | undefined) {
  if (!value) return "未记录";
  return (
    {
      external_local_agent: "本地研究助手",
      local_runtime: "本机执行",
      local: "本机",
      local_project: "本地项目目录",
      filesystem: "项目文件夹",
      assisted: "助手协作模式",
      guided: "引导模式",
      quick: "快捷模式",
      expert: "专家模式",
      isolated: "逐仓",
      cross: "全仓",
      stop_loss: "止损",
      liquidation: "强平",
      take_profit: "止盈",
      exit_signal: "离场信号",
      protective_stop: "保护性止损",
      conservative_zero_if_missing: "缺失时按 0 保守记录",
      adverse_nonzero_proxy: "缺失时采用不利的非零估计",
      capability_gated_modern_models_only: "仅允许满足能力要求的现代模型",
      local_one_shot_worker_v1: "本地单次任务资源策略",
    }[value] ?? value.replaceAll("_", " ")
  );
}
