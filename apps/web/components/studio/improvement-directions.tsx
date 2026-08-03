import { ImprovementDirection } from "@/lib/api";
import {
  cnComponentType,
  cnEvidence,
  cnMetric,
  cnParameter,
  cnStatus,
} from "@/components/studio/studio-labels";
import {
  EmptyState,
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

function readableValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") {
    return value.toLocaleString("zh-CN", {
      maximumFractionDigits: 8,
    });
  }
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(readableValue).join("、");
  return JSON.stringify(value, null, 2);
}

function readableParameterValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(readableParameterValue).join("、");
  }
  if (typeof value !== "string") return readableValue(value);
  return (
    {
      current: "当前设置",
      tighter: "更严格",
      looser: "更宽松",
      both: "多空都允许",
      long_only: "仅做多",
      short_only: "仅做空",
      exclude_long: "排除做多",
      exclude_short: "排除做空",
      higher: "提高筛选强度",
    }[value] ?? value
  );
}

function readableCost(key: string, value: unknown): string {
  if (key === "execution") {
    return value === "taker" ? "吃单成交" : readableValue(value);
  }
  if (
    typeof value === "number" &&
    ["fee_per_side", "taker_fee_per_side", "maker_fee_per_side"].includes(key)
  ) {
    return `${(value * 100).toLocaleString("zh-CN", {
      maximumFractionDigits: 4,
    })}% / 单边`;
  }
  if (key === "slippage_bps_per_side") {
    return `${readableValue(value)} bps / 单边`;
  }
  return readableValue(value);
}

function costLabel(key: string): string {
  return (
    {
      execution: "成交方式",
      fee_per_side: "单边手续费",
      taker_fee_per_side: "单边吃单手续费",
      maker_fee_per_side: "单边挂单手续费",
      slippage_bps_per_side: "单边滑点",
    }[key] ?? key
  );
}

function constraintOperator(value: string): string {
  return (
    {
      gte: "≥",
      gt: ">",
      lte: "≤",
      lt: "<",
      eq: "=",
    }[value] ?? value
  );
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1 border-b border-white/[0.06] py-2.5 last:border-0 md:grid-cols-[9rem_minmax(0,1fr)]">
      <div className="text-slate-500">{label}</div>
      <div className="min-w-0 whitespace-pre-wrap break-words text-slate-200">
        {children}
      </div>
    </div>
  );
}

export function ImprovementDirections({
  directions,
  providerConfigured,
  pending,
  onApprove,
  onBudgetChange,
}: {
  directions: ImprovementDirection[];
  providerConfigured: boolean;
  pending: boolean;
  onApprove: (proposalId: string) => void;
  onBudgetChange: (proposalId: string, trials: number, minutes: number) => void;
}) {
  if (!directions.length) {
    return (
      <EmptyState>
        {providerConfigured
          ? "尚无结构化改进方向。"
          : "网页模型未配置时不会伪造 AI 建议。可从上方诊断假设形成待审阅方案，或由本地研究助手创建。"}
      </EmptyState>
    );
  }

  return (
    <div className="space-y-3">
      {directions.slice(0, 3).map((direction, index) => {
        const actionable =
          direction.status === "draft" ||
          direction.status === "waiting_approval";
        return (
          <article
            key={direction.id}
            className="min-w-0 rounded-xl border border-white/10 bg-white/[0.02] p-4 text-sm"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs font-medium text-sky-200">
                  改进假设 H{index + 1} · 同一策略的候选版本
                </div>
                <div className="mt-1 font-medium leading-6 text-slate-100">
                  {direction.hypothesis}
                </div>
              </div>
              <StatusBadge
                value={cnStatus(direction.status)}
                tone={actionable ? "warning" : "neutral"}
              />
            </div>

            <div className="mt-3 grid gap-2 text-xs text-slate-400 sm:grid-cols-4">
              <span>{direction.estimated_trials ?? "—"} 个参数组合</span>
              <span>{direction.estimated_minutes ?? "—"} 分钟预算</span>
              <span>{direction.parameter_space.length} 个可变参数</span>
              <span>{direction.evidence_refs.length} 条来源证据</span>
            </div>

            <div className="mt-3 rounded-lg border border-sky-300/15 bg-sky-300/[0.05] px-3 py-2 text-xs leading-5 text-sky-100/80">
              冻结基准不会改变。确认后只创建 1 个候选版本；
              下方多个参数组合都是参数试验，不会创建多条新策略。
            </div>

            <details className="mt-3 rounded-xl border border-white/[0.08] bg-black/10">
              <summary className="flex min-h-11 cursor-pointer select-none items-center px-3 text-sm font-medium text-slate-200">
                查看改动、证据与研究预算
              </summary>
              <div className="border-t border-white/[0.08] px-3 pb-3 text-xs">
                <DetailRow label="规则改动">
                  {Object.entries(direction.rule_diff).map(([key, value]) => {
                    let display = readableValue(value);
                    if (key === "component_type" && typeof value === "string") {
                      display = cnComponentType(value);
                    }
                    if (
                      key === "contamination_status" &&
                      typeof value === "string"
                    ) {
                      display = cnEvidence(value);
                    }
                    if (
                      key === "parameter_changes" &&
                      value &&
                      typeof value === "object" &&
                      !Array.isArray(value)
                    ) {
                      display = Object.entries(value)
                        .map(
                          ([name, values]) =>
                            `${cnParameter(name)}：${readableParameterValue(values)}`,
                        )
                        .join("\n");
                    }
                    return (
                      <div key={key}>
                        <span className="text-slate-500">
                          {key === "parameter_changes"
                            ? "参数变化"
                            : key === "change_scope"
                              ? "改动范围"
                              : key === "expected_improvement"
                                ? "预期改善"
                                : key === "contamination_status"
                                  ? "证据边界"
                                  : key === "component_type"
                                    ? "组件类型"
                                    : key === "research_contract_source"
                                      ? "研究配置来源"
                                      : key}
                          ：
                        </span>
                        {display}
                      </div>
                    );
                  })}
                </DetailRow>
                <DetailRow label="参数范围">
                  {direction.parameter_space.map((item) => (
                    <div key={item.name}>
                      {cnParameter(item.name)}：
                      {readableParameterValue(item.values)}
                    </div>
                  ))}
                </DetailRow>
                <DetailRow label="数据切分">
                  {Object.entries(direction.data_splits).map(([key, value]) => (
                    <div key={key}>
                      {key === "train"
                        ? "训练区间"
                        : key === "validation"
                          ? "验证区间"
                          : "最终保留区间"}
                      ：{value}
                    </div>
                  ))}
                </DetailRow>
                <DetailRow label="成本模型">
                  {Object.entries(direction.cost_model).map(([key, value]) => (
                    <div key={key}>
                      {costLabel(key)}：{readableCost(key, value)}
                    </div>
                  ))}
                </DetailRow>
                <DetailRow label="目标与约束">
                  {direction.objectives.map((item) => (
                    <div key={`${item.metric}-${item.direction}`}>
                      优先目标：{cnMetric(item.metric)}
                      {item.direction === "maximize" ? "越高越好" : "越低越好"}
                    </div>
                  ))}
                  {direction.constraints.map((item) => (
                    <div key={`${item.metric}-${item.operator}`}>
                      约束：{cnMetric(item.metric)}{" "}
                      {constraintOperator(item.operator)}{" "}
                      {readableValue(item.value)}
                    </div>
                  ))}
                </DetailRow>
                <DetailRow label="失败条件">
                  {direction.failure_conditions.map((item) => (
                    <div key={item}>• {item}</div>
                  ))}
                </DetailRow>
                <DetailRow label="停止条件">
                  {direction.stopping_conditions.map((item) => (
                    <div key={item}>• {item}</div>
                  ))}
                </DetailRow>
                <DetailRow label="回滚方案">
                  {direction.rollback_plan}
                </DetailRow>

                {actionable ? (
                  <div className="mt-3 space-y-3">
                    <form
                      className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]"
                      onSubmit={(event) => {
                        event.preventDefault();
                        const form = new FormData(event.currentTarget);
                        onBudgetChange(
                          direction.id,
                          Number(form.get("trials")),
                          Number(form.get("minutes")),
                        );
                      }}
                    >
                      <label className="space-y-1 text-slate-400">
                        <span>参数组合上限</span>
                        <input
                          name="trials"
                          type="number"
                          min={1}
                          defaultValue={direction.estimated_trials ?? 20}
                          className="min-h-11 w-full min-w-0 rounded-lg border border-white/10 bg-[#071017] px-3 text-base text-slate-200"
                        />
                      </label>
                      <label className="space-y-1 text-slate-400">
                        <span>计算分钟上限</span>
                        <input
                          name="minutes"
                          type="number"
                          min={1}
                          defaultValue={direction.estimated_minutes ?? 45}
                          className="min-h-11 w-full min-w-0 rounded-lg border border-white/10 bg-[#071017] px-3 text-base text-slate-200"
                        />
                      </label>
                      <button
                        type="submit"
                        disabled={pending}
                        className="min-h-11 self-end rounded-lg border border-white/10 px-4 text-slate-200 disabled:opacity-40"
                      >
                        更新预算
                      </button>
                    </form>
                    <button
                      type="button"
                      onClick={() => onApprove(direction.id)}
                      disabled={pending}
                      className="min-h-11 w-full rounded-lg border border-emerald-300/30 bg-emerald-300/10 px-4 py-2 font-medium text-emerald-100 disabled:opacity-40"
                    >
                      确认创建候选版本并批量测试{" "}
                      {direction.estimated_trials ?? "有限"} 个参数组合
                    </button>
                    <p className="leading-5 text-slate-500">
                      这是独立审批点：会批准当前规则差异和预算，但不会使用最终保留测试、自动晋升或启动实盘。
                    </p>
                  </div>
                ) : null}
              </div>
            </details>

            <TechnicalDetails>
              <TechnicalId label="改进方案对象" value={direction.id} />
              {direction.baseline_version_id ? (
                <TechnicalId
                  label="冻结基准对象"
                  value={direction.baseline_version_id}
                />
              ) : null}
              {direction.candidate_version_id ? (
                <TechnicalId
                  label="候选版本对象"
                  value={direction.candidate_version_id}
                />
              ) : null}
            </TechnicalDetails>
          </article>
        );
      })}
    </div>
  );
}
