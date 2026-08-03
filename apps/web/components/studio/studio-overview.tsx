import { ResearchHandoff } from "@/lib/api";
import { cnStatus, friendlyStrategyName } from "@/components/studio/studio-labels";
import {
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

export type StrategyConclusion = {
  tone: "danger" | "success" | "neutral";
  title: string;
  detail: string;
};

export function StudioOverview({
  strategyTitle,
  researchMode,
  assistantEntry,
  executionStatus,
  conclusion,
  primaryActionLabel,
  primaryActionPending = false,
  primaryActionDisabled = false,
  onNext,
}: {
  strategyTitle: string | undefined;
  researchMode: string | undefined;
  assistantEntry: string;
  executionStatus: string;
  conclusion: StrategyConclusion;
  primaryActionLabel: string;
  primaryActionPending?: boolean;
  primaryActionDisabled?: boolean;
  onNext: () => void;
}) {
  const tone = conclusion.tone === "success"
    ? "positive"
    : conclusion.tone === "danger"
      ? "danger"
      : "neutral";
  return (
    <section
      data-testid="strategy-conclusion-card"
      className="rounded-2xl border border-white/10 bg-[linear-gradient(145deg,rgba(61,214,176,.09),rgba(10,21,30,.92)_48%)] p-4 md:p-5"
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-[11px] font-semibold tracking-[0.16em] text-emerald-300">
              当前研究
            </div>
            <StatusBadge value={conclusion.title} tone={tone} />
          </div>
          <h1 className="mt-2 max-w-4xl break-words text-xl font-semibold tracking-tight text-white [overflow-wrap:anywhere] md:text-2xl">
            {friendlyStrategyName(strategyTitle)}
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            {conclusion.detail}
          </p>
        </div>
        <div className="flex shrink-0 flex-col gap-3 lg:items-end">
          <div className="flex flex-wrap gap-2 text-xs lg:max-w-[420px] lg:justify-end">
            <StatusBadge value={researchMode ?? "引导模式"} />
            <StatusBadge value={assistantEntry} />
            <StatusBadge value={executionStatus} tone="warning" />
          </div>
          <button
            type="button"
            onClick={onNext}
            disabled={primaryActionDisabled || primaryActionPending}
            className="min-h-11 w-full cursor-pointer rounded-xl bg-emerald-300 px-4 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-200 sm:w-auto"
          >
            {primaryActionPending ? "正在处理…" : primaryActionLabel}
          </button>
        </div>
      </div>
    </section>
  );
}

export function ResearchHandoffPanel({
  handoff,
  versioningAvailable,
}: {
  handoff: ResearchHandoff | undefined;
  versioningAvailable: boolean;
}) {
  const handoffPresentation = presentHandoff(handoff);
  const actionRequired =
    handoff?.user_action_required ||
    handoff?.stop_reason_code === "worker_job_failed" ||
    handoff?.stop_reason_code === "authorized_pipeline_execution_failed";
  return (
    <section
      data-testid="research-handoff-card"
      className="rounded-2xl border border-white/[0.08] bg-black/10 p-4"
    >
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px]">
        <div>
          <div className="text-sm font-medium text-amber-100">为什么停在这里</div>
          <p className="mt-1 text-sm leading-6 text-slate-300">
            {handoffPresentation.reason}
          </p>
          <div className="mt-3 text-sm font-medium text-emerald-100">
            建议下一步
          </div>
          <p className="mt-1 text-sm leading-6 text-slate-300">
            {handoffPresentation.nextAction}
          </p>
        </div>
        <div className="rounded-xl border border-white/10 bg-black/10 p-3 text-xs">
          <div className="text-slate-500">是否需要你操作</div>
          <div className="mt-2 leading-5 text-slate-200">
            {actionRequired
              ? handoffPresentation.userAction
              : "当前不需要用户操作。"}
          </div>
        </div>
      </div>

      {handoff ? (
        <TechnicalDetails label="展开已完成、未执行与审计对象">
          <div>
            <div className="text-emerald-200">已完成</div>
            <ul className="mt-1 space-y-1">
              {handoff.completed_actions.map((item) => (
                <li key={item}>· {item}</li>
              ))}
            </ul>
          </div>
          <div>
            <div className="text-slate-300">未执行</div>
            <ul className="mt-1 space-y-1">
              {handoff.not_started_actions.map((item) => (
                <li key={item}>· {item}</li>
              ))}
            </ul>
          </div>
          <TechnicalId
            label="当前研究对象"
            value={handoff.approval_subject_id ?? handoff.subject_id}
          />
          <div>
            <div className="text-slate-300">原始停止说明</div>
            <p className="mt-1">{handoff.stop_reason_text}</p>
          </div>
          <div>
            <div className="text-slate-300">原始下一步</div>
            <p className="mt-1">{handoff.next_recommended_action}</p>
          </div>
          {handoff.required_user_action ? (
            <div>
              <div className="text-slate-300">原始用户操作</div>
              <p className="mt-1">{handoff.required_user_action}</p>
            </div>
          ) : null}
          <div>
            版本规则：
            {versioningAvailable
              ? "本地权威状态为准，Git 仅手动备份"
              : "版本策略暂不可用"}
          </div>
        </TechnicalDetails>
      ) : null}
    </section>
  );
}

function presentHandoff(handoff: ResearchHandoff | undefined) {
  if (!handoff) {
    return {
      reason: "当前会话尚无结构化停止记录。",
      nextAction: "保存策略后，系统会显示明确的研究结论与下一动作。",
      userAction: "当前不需要用户操作。",
    };
  }
  if (
    handoff.stop_reason_code === "worker_job_failed" ||
    handoff.stop_reason_code === "authorized_pipeline_execution_failed"
  ) {
    const missingPipelineMetadata = handoff.stop_reason_text.includes(
      "reviewed StrategySpec has no authorized pipeline metadata",
    );
    return {
      reason: missingPipelineMetadata
        ? "这次任务由旧版执行程序运行，未找到当前策略对应的受控自动研究配置。失败状态和已有证据都已保留。"
        : /[\u3400-\u9fff]/.test(handoff.stop_reason_text)
          ? handoff.stop_reason_text
          : "后台研究任务执行失败，原始技术原因已保留在下方详情中。",
      nextAction: missingPipelineMetadata
        ? "确认本地服务已重启到最新代码后，前往“后台任务”显式重试原批准范围；不会扩大预算或使用最终保留测试。"
        : "前往“后台任务”查看中文原因；能够安全重试时页面会显示重试按钮，否则回到本研究重新授权或停止。",
      userAction: "需要。请按建议下一步处理失败任务。",
    };
  }
  const known: Record<
    string,
    { reason: string; nextAction: string; userAction?: string }
  > = {
    minimum_reward_hypothesis_rejected: {
      reason:
        "用户已明确拒绝“最低目标空间（R）”改进方案。三档诊断对比已完成；冻结基准未改变，组件证据仅保留为诊断性记录。",
      nextAction:
        "关闭本假设，不再继续微调最低目标空间。只有选定新的、彼此独立的单组件假设后，才创建新方案。",
    },
    diagnostic_batch_hypothesis_failed: {
      reason:
        "追加式修正已完成此前受阻的两个参数方案。降低最低目标空间后，交易数增加，但验证净收益、盈亏效率、单笔期望与回撤均变差，未形成通过约束或稳定区间。",
      nextAction:
        "建议拒绝当前精确方案，保留诊断证据，不改变冻结基准，并停止这一假设。",
      userAction: "请对当前精确方案执行批准或拒绝；建议拒绝。",
    },
    diagnostic_trial_contract_mismatch: {
      reason:
        "已批准的 0.8R 与 0.6R 方案被旧版应用层最小值门槛阻止；1.0R 已完成，受阻记录均已保留。契约虽已修复，但不能静默重跑原参数签名。",
      nextAction:
        "只有在精确批准追加式修正执行后，才运行尚未完成的 0.8R 与 0.6R 方案；继续禁止最终保留测试。",
      userAction: "请对当前精确实验计划批准或拒绝追加式修正执行。",
    },
    worker_job_scope_completed: {
      reason: "后台任务已完成获批范围，系统没有自动进入后续阶段。",
      nextAction: "先审阅结果与门槛证据，再决定是否批准下一阶段。",
    },
  };
  const matched = known[handoff.stop_reason_code];
  if (matched) {
    return {
      reason: matched.reason,
      nextAction: matched.nextAction,
      userAction:
        matched.userAction ??
        (handoff.user_action_required
          ? "请对技术详情中的精确研究对象完成所需操作。"
          : "当前不需要用户操作。"),
    };
  }
  const reasonHasChinese = /[\u3400-\u9fff]/.test(handoff.stop_reason_text);
  const actionHasChinese = /[\u3400-\u9fff]/.test(
    handoff.next_recommended_action,
  );
  return {
    reason: reasonHasChinese
      ? handoff.stop_reason_text
      : `研究流程当前状态：${cnStatus(handoff.status)}。原始停止说明已保留在技术详情中。`,
    nextAction: actionHasChinese
      ? handoff.next_recommended_action
      : handoff.user_action_required
        ? "请先核对技术详情中的精确研究对象，再完成明确要求的操作。"
        : "本次范围已经停止；只有选择新的独立假设或明确批准下一阶段后才继续。",
    userAction: handoff.user_action_required
      ? "需要。请按技术详情中的精确研究对象完成操作。"
      : "当前不需要用户操作。",
  };
}

export function buildTopConclusion({
  hasBaseline,
  outcome,
  viability,
  suitableRegimeCount,
  regimeCount,
}: {
  hasBaseline: boolean;
  outcome: string | undefined;
  viability: string | undefined;
  suitableRegimeCount: number;
  regimeCount: number;
}): StrategyConclusion {
  if (!hasBaseline) {
    return {
      tone: "neutral",
      title: "尚未形成策略结论",
      detail: "先保存并冻结原样基准，再运行有边界的初筛。",
    };
  }
  if (outcome === "rejected" || viability === "failed") {
    if (suitableRegimeCount === 0 && regimeCount > 0) {
      return {
        tone: "danger",
        title: "当前未发现适合行情",
        detail:
          "策略未通过继续研究门槛，行情初查也没有形成正向证据。停止完整策略调参，只保留有独立证据的局部组件。",
      };
    }
    return {
      tone: "danger",
      title: "当前策略未通过继续研究门槛",
      detail: "停止昂贵验证；只复用已有结果做廉价归因和局部组件假设。",
    };
  }
  if (viability === "passed") {
    return {
      tone: "success",
      title: "已通过初步可行性门槛",
      detail: "仍需成本敏感性、行情验证和独立最终保留测试，不能视为可实盘策略。",
    };
  }
  return {
    tone: "neutral",
    title: "等待可行性结论",
    detail: "当前只展示本会话和冻结基准的证据，不混入其他策略结果。",
  };
}
