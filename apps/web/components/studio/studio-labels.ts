import { Trial } from "@/lib/api";

const labels: Record<string, string> = {
  quick: "快捷模式",
  guided: "引导模式",
  expert: "专家模式",
  correctness: "规则与数据检查",
  smoke: "小范围试跑",
  fast_screen: "快速初筛",
  viability: "可行性门槛",
  loss_attribution: "亏损原因分析",
  regime_diagnostic: "行情适配初查",
  component_hypothesis_generation: "局部改进方向",
  cheap_cost_sensitivity: "成本敏感性初查",
  regime_and_pine: "行情适配与 TradingView 语义核对",
  full_validation_and_locked_test: "完整验证与最终保留测试",
  full_stress: "完整压力测试",
  full_validation: "完整验证",
  locked_test: "最终保留测试",
  dry_run: "模拟运行",
  inbox: "待整理",
  formalized: "已形式化",
  baseline: "冻结基准",
  candidate: "候选快照",
  validated: "已验证",
  degraded: "已退化",
  retired: "已停用",
  draft: "草稿",
  awaiting_confirmation: "等待确认",
  baseline_frozen: "基准已冻结",
  queued: "排队中",
  running: "运行中",
  succeeded: "已完成",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  paused: "已暂停",
  waiting_approval: "等待批准",
  waiting_user_approval: "等待用户批准",
  waiting_required_input: "等待必要输入",
  completed_scope: "本次范围已完成",
  gate_failed: "研究门槛未通过",
  budget_exhausted: "预算已用完",
  blocked_dependency: "依赖不可用",
  safety_refusal: "安全门禁已拒绝",
  active: "生效中",
  approved: "已批准",
  executing: "执行中",
  evaluated: "已评估",
  accepted: "已接受",
  rejected: "未通过",
  expired: "已过期",
  passed: "通过",
  blocked: "被门禁阻止",
  not_evaluated: "尚未评估",
  not_recorded: "尚未记录",
  not_queued: "尚未排队",
  "not queued": "尚未排队",
  manual_handoff_required: "需手动交给 Codex",
  direct_interaction: "当前 Codex 直接交互",
  external_agent_direct: "直接在 Codex 研究",
  web_local_connector: "网页调用本地助手",
  web_provider: "网页模型",
  screening: "初步分析",
  insufficient_history: "历史不足",
  extended_validation: "扩展验证",
  not_tested: "尚未测试",
  diagnostic_improvement: "诊断性改进",
  component_candidate: "策略组件候选",
  strategy_candidate: "策略候选",
  research: "真实研究证据",
  fixture: "测试连线证据",
  unavailable: "证据不可用",
  screening_contaminated: "已观察样本，仅作诊断",
  diagnostic: "诊断证据",
  natural_language: "自然语言",
  pine: "Pine Script",
  file: "文件",
  external_agent: "本地研究助手",
  manual: "人工",
  deterministic_rule_analyzer: "规则分析器",
  entry: "入场组件",
  filter: "过滤组件",
  exit: "出场组件",
  risk: "风控组件",
  execution: "执行组件",
};

export function cnLabel(value: string | undefined) {
  if (!value) return "—";
  return labels[value] ?? value;
}

export const cnStatus = cnLabel;
export const cnStage = cnLabel;
export const cnProfile = cnLabel;
export const cnOutcome = cnLabel;
export const cnSource = cnLabel;
export const cnComponentType = cnLabel;
export const cnEvidence = cnLabel;
export const cnResearchMode = cnLabel;

export function cnGateReason(value: string) {
  if (value === "all viability thresholds passed") {
    return "全部可行性标准均已通过。";
  }
  if (value.startsWith("missing viability metrics:")) {
    const metrics = value
      .replace("missing viability metrics:", "")
      .split(",")
      .map((item) => cnMetric(item.trim()))
      .join("、");
    return `缺少可行性指标：${metrics}。`;
  }
  const comparison = value.match(
    /^([a-z_]+)=(-?\d+(?:\.\d+)?) required (>=|<=) (-?\d+(?:\.\d+)?)$/,
  );
  if (!comparison) return value;
  const [, metric, actualText, operator, thresholdText] = comparison;
  const actual = Number(actualText);
  const threshold = Number(thresholdText);
  return `${cnMetric(metric)}为 ${formatGateMetric(metric, actual)}，要求${
    operator === ">=" ? "不低于" : "不高于"
  } ${formatGateMetric(metric, threshold)}。`;
}

function formatGateMetric(metric: string, value: number) {
  if (
    metric === "validation_net_return" ||
    metric === "validation_expectancy" ||
    metric === "validation_max_drawdown_abs"
  ) {
    return formatPercent(value);
  }
  if (metric === "validation_trade_count") {
    return `${Math.round(value).toLocaleString("zh-CN")} 笔`;
  }
  return value.toFixed(2);
}

export function cnParameter(value: string) {
  return (
    {
      minimum_reward_r: "最低目标空间（R）",
      max_stop_distance_fraction: "最大止损距离",
      risk_per_trade_equity_fraction: "单笔账户风险",
      structure_exit_variant: "结构退出方式",
      entry_mode: "入场方式",
      max_reentries_per_trend_leg: "每段趋势最多再入次数",
      enabled_side: "允许交易方向",
      entry_selectivity: "入场选择性",
      exit_rule_variant: "退出规则",
    }[value] ?? value
  );
}

export function cnMetric(value: string) {
  return (
    {
      validation_net_return: "验证净收益",
      validation_profit_factor: "盈亏效率",
      validation_expectancy: "单笔期望",
      validation_max_drawdown_abs: "最大回撤",
      validation_trade_count: "交易数",
      incremental_net_return: "相对基准净收益",
    }[value] ?? value
  );
}

export function cnRegime(value: string) {
  const [trend, volatility] = value.split("__");
  const trendLabel =
    {
      trend_up: "上涨趋势",
      trend_down: "下跌趋势",
      trend_neutral: "震荡",
      bull: "多头",
      bear: "空头",
      transition: "切换期",
    }[trend] ?? trend;
  const volatilityLabel =
    {
      low_vol: "低波动",
      normal_vol: "正常波动",
      high_vol: "高波动",
    }[volatility] ?? volatility;
  return volatility ? `${trendLabel} · ${volatilityLabel}` : trendLabel;
}

export function formatTrialParameters(parameters: Record<string, unknown>) {
  return Object.entries(parameters)
    .map(([key, value]) => {
      if (value === undefined || value === null) {
        return `${cnParameter(key)}：未记录`;
      }
      const displayValue =
        {
          current: "当前基准",
          tighter: "更严格",
          looser: "更宽松",
          confirmation_candle_breakout: "确认 K 线突破",
          both: "多空双向",
          higher: "提高选择性",
        }[String(value)] ?? String(value);
      return `${cnParameter(key)}：${displayValue}`;
    })
    .join("；");
}

export function trialDelta(trial: Trial, allTrials: Trial[]) {
  if (Number.isFinite(trial.metrics.incremental_net_return)) {
    return trial.metrics.incremental_net_return;
  }
  if (Number.isFinite(trial.metrics.validation_net_return_delta_vs_baseline)) {
    return trial.metrics.validation_net_return_delta_vs_baseline;
  }
  const baselineTrial = allTrials.find(
    (item) =>
      item.metrics.incremental_net_return === 0 ||
      Object.values(item.parameters).some((value) =>
        ["current", "baseline"].includes(String(value)),
      ),
  );
  const value = trial.metrics.validation_net_return;
  const baselineValue = baselineTrial?.metrics.validation_net_return;
  return typeof value === "number" &&
    Number.isFinite(value) &&
    typeof baselineValue === "number" &&
    Number.isFinite(baselineValue)
    ? value - baselineValue
    : Number.NaN;
}

export function trialConclusion(trial: Trial, allTrials: Trial[]) {
  if (trial.status !== "succeeded") return "运行失败";
  const net = trial.metrics.validation_net_return;
  const profitFactor = trial.metrics.validation_profit_factor;
  const expectancy = trial.metrics.validation_expectancy;
  if (net >= 0 && profitFactor >= 1 && expectancy > 0) return "可进入下一步验证";
  const delta = trialDelta(trial, allTrials);
  if (delta > 0) return "有改善，但还不能使用";
  if (delta < 0) return "比基准更差";
  if (![net, profitFactor, expectancy].every(Number.isFinite)) {
    return "旧结果指标不完整";
  }
  return "当前基准";
}

export function formatPercent(value: number | undefined) {
  return Number.isFinite(value) ? `${((value ?? 0) * 100).toFixed(2)}%` : "—";
}

export function formatSignedPercent(value: number) {
  if (!Number.isFinite(value)) return "—";
  const percent = value * 100;
  return `${percent > 0 ? "+" : ""}${percent.toFixed(2)}%`;
}

export function formatNumber(value: number | undefined, digits: number) {
  return Number.isFinite(value) ? (value ?? 0).toFixed(digits) : "—";
}

export function friendlyStrategyName(title: string | undefined) {
  return title?.trim() || "未命名策略研究";
}

export function cnEvent(value: string) {
  return (
    {
      "research_session.created": "创建研究会话",
      "research_session.mode_updated": "更新研究模式",
      "message.created": "保存用户输入",
      "strategy_draft.created": "保存策略草稿",
      "strategy_baseline.frozen": "冻结基准版本",
      "experiment_plan.created": "创建实验计划",
      "experiment_plan.approved": "批准实验计划",
      "agent_run.created": "创建研究助手任务",
      "research_handoff.recorded": "记录停止原因与下一步",
      "component_candidate.archived": "归档策略组件",
      "component_candidate.restored": "恢复策略组件",
    }[value] ?? value.replaceAll("_", " ").replaceAll(".", " · ")
  );
}

export function cnActor(value: string) {
  return (
    {
      user: "用户",
      system: "系统",
      external_agent: "本地研究助手",
      embedded_agent: "网页研究助手",
      worker: "确定性执行程序",
    }[value] ?? value
  );
}
