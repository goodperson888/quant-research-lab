"use client";

import { useQueries } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch, RunBundle, RunBundleChart, Trial } from "@/lib/api";
import {
  formatPercent,
  formatTrialParameters,
} from "@/components/studio/studio-labels";
import {
  EmptyState,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";
import {
  InteractiveEquityChart,
  InteractiveTradingChart,
} from "@/components/studio/interactive-research-charts";

export function PerformanceCharts({ bundles }: { bundles: RunBundle[] }) {
  const [selectedBundleId, setSelectedBundleId] = useState<string | null>(null);
  const [marketTimeframe, setMarketTimeframe] = useState("1h");
  const chartQueries = useQueries({
    queries: bundles.slice(0, 8).map((bundle) => ({
      queryKey: [
        "run-bundle-chart",
        bundle.bundle_id,
        marketTimeframe,
      ],
      queryFn: () =>
        apiFetch<RunBundleChart>(
          `/api/run-bundles/${encodeURIComponent(bundle.bundle_id)}/chart-series?max_points=2000&market_timeframe=${encodeURIComponent(marketTimeframe)}`,
        ),
      retry: false,
    })),
  });
  const availableCharts = chartQueries.flatMap((query, index) =>
    query.data?.available && query.data.series.length
      ? [
          {
            bundle: bundles[index],
            chart: query.data,
            index,
          },
        ]
      : [],
  );
  const selectedChartEntry =
    availableCharts.find(
      (item) => item.bundle?.bundle_id === selectedBundleId,
    ) ??
    availableCharts.find(
      (item) => item.chart.market_series && item.chart.trades.length,
    ) ??
    availableCharts[0];
  const chart = selectedChartEntry?.chart;
  const selectedBundle = selectedChartEntry?.bundle;
  const availableMarketTimeframes =
    chart?.available_market_timeframes ?? [];
  const isLoading = chartQueries.some((query) => query.isPending);
  const unavailableReason = chartQueries
    .map((query) => query.data?.reason)
    .find(Boolean);
  const allSeries = chartQueries
    .flatMap((query) => (query.data?.available ? query.data.series : []))
    .slice(0, 6);
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const coloredSeries = allSeries.map((item, index) => ({
    ...item,
    color: CHART_COLORS[index % CHART_COLORS.length],
  }));
  const series = coloredSeries.filter(
    (item) => visible[item.series_id] !== false,
  );

  return (
    <div className="space-y-5">
      {availableCharts.length ? (
        <>
          {selectedBundle && selectedChartEntry.index > 0 ? (
            <div className="rounded-lg border border-sky-300/15 bg-sky-300/[0.07] px-3 py-2 text-xs leading-5 text-sky-100">
              最新报告未保存资金曲线，已自动采用最近一份有真实曲线的研究结果。
            </div>
          ) : null}
          <PerformanceSummaryTable series={coloredSeries} />
          <div className="flex flex-wrap gap-2">
            {coloredSeries.map((item) => (
              <label
                key={item.series_id}
                className="flex min-h-11 cursor-pointer items-center gap-2 rounded-full border border-white/10 px-3 py-2 text-xs text-slate-300 transition hover:border-white/20 hover:bg-white/[0.03]"
              >
                <input
                  type="checkbox"
                  checked={visible[item.series_id] !== false}
                  onChange={(event) =>
                    setVisible((current) => ({
                      ...current,
                      [item.series_id]: event.target.checked,
                    }))
                  }
                />
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: item.color }}
                />
                {item.label}
              </label>
            ))}
          </div>
          {availableCharts.length > 1 ? (
            <label className="block max-w-xl">
              <span className="mb-1 block text-xs text-slate-500">
                行情交易图当前策略
              </span>
              <select
                value={selectedBundle?.bundle_id ?? ""}
                onChange={(event) => setSelectedBundleId(event.target.value)}
                className="min-h-11 w-full rounded-xl border border-white/10 bg-[#071017] px-3 text-sm text-slate-200"
              >
                {availableCharts.map((item) => (
                  <option
                    key={item.bundle?.bundle_id ?? item.chart.bundle_id}
                    value={item.bundle?.bundle_id ?? item.chart.bundle_id}
                  >
                    {item.chart.series.map((entry) => entry.label).join(" / ")}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-[11px] leading-5 text-slate-500">
                多策略资金曲线可以同时比较；买卖点只显示当前选中策略，避免互相覆盖。
              </span>
            </label>
          ) : null}
          {availableMarketTimeframes.length ? (
            <MarketTimeframeSelector
              value={marketTimeframe}
              options={availableMarketTimeframes}
              onChange={setMarketTimeframe}
            />
          ) : null}
          {chart?.market_series ? (
            <InteractiveTradingChart
              market={chart.market_series}
              trades={chart.trades}
              strategyLabel={chart.series.map((item) => item.label).join(" / ")}
            />
          ) : null}
          {!chart?.market_series && chart?.market_reason ? (
            <div className="text-xs leading-5 text-amber-100/80">
              行情对照暂不可用：{chart.market_reason}
            </div>
          ) : null}
          {series.length ? (
            <InteractiveEquityChart
              series={series.map((item) => ({
                id: item.series_id,
                label: item.label,
                color: item.color,
                points: item.points.map((point) => ({
                  t: point.t,
                  value: point.normalized_equity,
                })),
              }))}
            />
          ) : (
            <EmptyState>
              当前已隐藏全部资金曲线。重新勾选上方任意一条曲线即可继续比较。
            </EmptyState>
          )}
          <TechnicalDetails label="曲线来源与限制">
            {(chart?.limitations ?? []).map((item) => (
              <p key={item}>{item}</p>
            ))}
            {(chart?.trade_source_artifact_keys ?? []).map((artifactKey) => (
              <TechnicalId
                key={artifactKey}
                label="逐笔交易"
                value={artifactKey}
              />
            ))}
            {allSeries.map((item) => (
              <TechnicalId
                key={item.series_id}
                label={item.label}
                value={item.source_artifact_key}
              />
            ))}
          </TechnicalDetails>
        </>
      ) : (
        <EmptyState>
          {isLoading
            ? "正在读取当前策略的真实资金曲线……"
            : unavailableReason ??
              "当前策略还没有保存可读取的逐时点资金曲线。下次运行快速初筛或完整回测后，这里会自动显示折线图。"}
        </EmptyState>
      )}
    </div>
  );
}

function MarketTimeframeSelector({
  value,
  options,
  onChange,
}: {
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-black/10 p-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-medium text-slate-200">行情K线周期</div>
          <div className="mt-1 text-xs leading-5 text-slate-500">
            切换本次回测已登记的行情数据；交易时间与价格不会改变。
          </div>
        </div>
        <div
          className="flex max-w-full gap-2 overflow-x-auto pb-1"
          role="group"
          aria-label="选择行情K线周期"
        >
          {options.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={value === option}
              onClick={() => onChange(option)}
              className={
                value === option
                  ? "min-h-11 shrink-0 rounded-xl border border-sky-300/30 bg-sky-300/10 px-3 text-xs text-sky-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
                  : "min-h-11 shrink-0 rounded-xl border border-white/10 px-3 text-xs text-slate-400 transition hover:border-white/20 hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
              }
            >
              {timeframeLabel(option)}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function PerformanceSummaryTable({
  series,
}: {
  series: Array<RunBundleChart["series"][number] & { color: string }>;
}) {
  return (
    <div className="overflow-x-auto rounded-xl border border-white/10">
      <table className="w-full min-w-[680px] border-collapse text-left text-xs tabular-nums">
        <thead className="bg-white/[0.04] text-slate-500">
          <tr>
            <th className="px-3 py-2.5 font-medium">曲线</th>
            <th className="px-3 py-2.5 font-medium">累计收益</th>
            <th className="px-3 py-2.5 font-medium">最大回撤</th>
            <th className="px-3 py-2.5 font-medium">数据点</th>
            <th className="px-3 py-2.5 font-medium">覆盖区间</th>
          </tr>
        </thead>
        <tbody>
          {series.map((item) => {
            const first = item.points[0];
            const last = item.points[item.points.length - 1];
            const maxDrawdown = Math.min(
              0,
              ...item.points.map((point) => point.drawdown),
            );
            return (
              <tr
                key={item.series_id}
                className="border-t border-white/[0.06] text-slate-300"
              >
                <td className="px-3 py-3">
                  <span className="flex items-center gap-2 text-slate-100">
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: item.color }}
                    />
                    {item.label}
                  </span>
                </td>
                <td className="px-3 py-3">
                  {formatSignedPercentValue(
                    (last?.normalized_equity ?? 1) - 1,
                  )}
                </td>
                <td className="px-3 py-3 text-amber-200">
                  {formatSignedPercentValue(maxDrawdown)}
                </td>
                <td className="px-3 py-3">
                  {item.points.length.toLocaleString("zh-CN")}
                </td>
                <td className="px-3 py-3 text-slate-400">
                  {first && last
                    ? `${formatShortDate(first.t)} — ${formatShortDate(last.t)}`
                    : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const TRIAL_METRICS = [
  {
    key: "validation_net_return",
    label: "验证收益",
    baseline: 0,
    format: formatSignedPercentValue,
    colorMode: "signed",
  },
  {
    key: "validation_max_drawdown_abs",
    label: "最大回撤",
    baseline: 0,
    format: (value: number) => formatPercent(value),
    colorMode: "drawdown",
  },
  {
    key: "validation_profit_factor",
    label: "盈亏效率",
    baseline: 1,
    format: (value: number) => value.toFixed(2),
    colorMode: "profit_factor",
  },
  {
    key: "validation_trade_count",
    label: "交易数",
    baseline: 0,
    format: (value: number) => Math.round(value).toLocaleString("zh-CN"),
    colorMode: "neutral",
  },
] as const;

export function TrialMetricComparison({ trials }: { trials: Trial[] }) {
  const [metricKey, setMetricKey] =
    useState<(typeof TRIAL_METRICS)[number]["key"]>("validation_net_return");
  const succeeded = trials
    .filter((item) => item.status === "succeeded")
    .slice(0, 20);
  if (!succeeded.length) {
    return (
      <EmptyState>
        尚无成功参数方案可比较。批量任务完成后，这里会先显示净收益、回撤和交易数的相对差异。
      </EmptyState>
    );
  }
  const metric =
    TRIAL_METRICS.find((item) => item.key === metricKey) ?? TRIAL_METRICS[0];
  const parameterKeys = Array.from(
    new Set(succeeded.flatMap((trial) => Object.keys(trial.parameters))),
  );
  const varyingParameterKeys = parameterKeys.filter(
    (key) =>
      new Set(
        succeeded.map((trial) => JSON.stringify(trial.parameters[key])),
      ).size > 1,
  );
  const numericParameter =
    varyingParameterKeys.length === 1 &&
    succeeded.every(
      (trial) =>
        typeof trial.parameters[varyingParameterKeys[0]] === "number" &&
        Number.isFinite(trial.parameters[varyingParameterKeys[0]]),
    )
      ? varyingParameterKeys[0]
      : null;
  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1 text-sm font-medium text-slate-200">
          参数方案指标对比
        </div>
        <div className="text-xs leading-5 text-slate-500">
          单个连续参数会按数值顺序绘制折线；多个参数同时变化时保留独立条形，避免用连线制造不存在的顺序。
        </div>
      </div>
      <div className="flex max-w-full gap-1 overflow-x-auto">
        {TRIAL_METRICS.map((item) => (
          <button
            type="button"
            key={item.key}
            onClick={() => setMetricKey(item.key)}
            className={
              metricKey === item.key
                ? "min-h-11 cursor-pointer whitespace-nowrap rounded-full border border-sky-300/25 bg-sky-300/10 px-3 py-2 text-xs text-sky-100"
                : "min-h-11 cursor-pointer whitespace-nowrap rounded-full border border-white/10 px-3 py-2 text-xs text-slate-400 hover:text-slate-200"
            }
          >
            {item.label}
          </button>
        ))}
      </div>
      {numericParameter ? (
        <SvgParameterLineChart
          parameterName={numericParameter}
          metricLabel={metric.label}
          baseline={metric.baseline}
          formatValue={metric.format}
          points={succeeded
            .map((trial) => ({
              trialId: trial.id,
              parameterValue: Number(trial.parameters[numericParameter]),
              metricValue: finiteOrZero(trial.metrics[metric.key]),
            }))
            .sort((left, right) => left.parameterValue - right.parameterValue)}
        />
      ) : (
        <TrialMetricBars
          trials={succeeded}
          metricKey={metric.key}
          formatValue={metric.format}
          colorMode={metric.colorMode}
        />
      )}
    </div>
  );
}

function TrialMetricBars({
  trials,
  metricKey,
  formatValue,
  colorMode,
}: {
  trials: Trial[];
  metricKey: (typeof TRIAL_METRICS)[number]["key"];
  formatValue: (value: number) => string;
  colorMode: (typeof TRIAL_METRICS)[number]["colorMode"];
}) {
  const values = trials.map((item) => finiteOrZero(item.metrics[metricKey]));
  const scale = Math.max(...values.map((value) => Math.abs(value)), 0.001);
  return (
    <div className="space-y-2">
      {trials.map((trial) => {
        const value = finiteOrZero(trial.metrics[metricKey]);
        return (
          <div
            key={trial.id}
            className="grid grid-cols-[minmax(0,1fr)_90px] gap-3 text-xs"
          >
            <div>
              <div className="truncate text-slate-300">
                {formatTrialParameters(trial.parameters)}
              </div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-white/[0.06]">
                <div
                  className={`${value < 0 ? "ml-auto " : ""}${trialMetricBarColor(
                    colorMode,
                    value,
                  )}`}
                  style={{
                    width: `${Math.max((Math.abs(value) / scale) * 100, 2)}%`,
                  }}
                />
              </div>
            </div>
            <div
              className={`text-right ${trialMetricTextColor(colorMode, value)}`}
            >
              {formatValue(value)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function trialMetricBarColor(
  colorMode: (typeof TRIAL_METRICS)[number]["colorMode"],
  value: number,
) {
  if (colorMode === "drawdown") return "h-full bg-amber-300/70";
  if (colorMode === "neutral") return "h-full bg-sky-300/70";
  if (colorMode === "profit_factor") {
    return value >= 1
      ? "h-full bg-emerald-300/70"
      : "h-full bg-rose-300/70";
  }
  return value >= 0
    ? "h-full bg-emerald-300/70"
    : "h-full bg-rose-300/70";
}

function trialMetricTextColor(
  colorMode: (typeof TRIAL_METRICS)[number]["colorMode"],
  value: number,
) {
  if (colorMode === "drawdown") return "text-amber-200";
  if (colorMode === "neutral") return "text-sky-200";
  if (colorMode === "profit_factor") {
    return value >= 1 ? "text-emerald-200" : "text-rose-200";
  }
  return value >= 0 ? "text-emerald-200" : "text-rose-200";
}

function finiteOrZero(value: number | undefined) {
  return Number.isFinite(value) ? (value ?? 0) : 0;
}

const CHART_COLORS = ["#3dd6b0", "#7dd3fc", "#fbbf24", "#fda4af", "#c4b5fd", "#fb923c"];

function SvgParameterLineChart({
  parameterName,
  metricLabel,
  points,
  baseline,
  formatValue,
}: {
  parameterName: string;
  metricLabel: string;
  points: Array<{
    trialId: string;
    parameterValue: number;
    metricValue: number;
  }>;
  baseline: number;
  formatValue: (value: number) => string;
}) {
  const width = 860;
  const height = 300;
  const padding = { top: 22, right: 24, bottom: 54, left: 64 };
  const parameterValues = points.map((point) => point.parameterValue);
  const metricValues = points.map((point) => point.metricValue);
  const xScale = paddedRange(parameterValues);
  const yScale = paddedRange([...metricValues, baseline]);
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const x = (value: number) =>
    padding.left +
    ((value - xScale.min) / Math.max(xScale.max - xScale.min, 0.000001)) *
      plotWidth;
  const y = (value: number) =>
    padding.top +
    ((yScale.max - value) /
      Math.max(yScale.max - yScale.min, 0.000001)) *
      plotHeight;
  const yTicks = valueTicks(yScale.min, yScale.max, 4);

  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-black/10 p-3">
      <div className="mb-1 text-sm font-medium text-slate-200">
        {parameterName} 与{metricLabel}
      </div>
      <div className="mb-3 text-xs leading-5 text-slate-500">
        横轴按参数实际数值排序；折线只表示这一项连续参数的局部变化趋势。
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${parameterName} 与${metricLabel}参数对比`}
        className="h-auto w-full"
      >
        {yTicks.map((value) => (
          <g key={value}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={y(value)}
              y2={y(value)}
              stroke="rgba(148,163,184,.12)"
            />
            <text
              x={padding.left - 8}
              y={y(value) + 4}
              textAnchor="end"
              fill="rgba(148,163,184,.72)"
              fontSize="11"
            >
              {formatValue(value)}
            </text>
          </g>
        ))}
        <line
          x1={padding.left}
          x2={width - padding.right}
          y1={y(baseline)}
          y2={y(baseline)}
          stroke="rgba(148,163,184,.28)"
          strokeDasharray="5 5"
        />
        <polyline
          points={points
            .map(
              (point) =>
                `${x(point.parameterValue)},${y(point.metricValue)}`,
            )
            .join(" ")}
          fill="none"
          stroke="#7dd3fc"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
        {points.map((point, index) => (
          <g key={point.trialId}>
            <circle
              cx={x(point.parameterValue)}
              cy={y(point.metricValue)}
              r="4"
              fill="#071017"
              stroke="#7dd3fc"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={x(point.parameterValue)}
              y={height - 24 - (index % 2) * 13}
              textAnchor="middle"
              fill="rgba(148,163,184,.78)"
              fontSize="11"
            >
              {formatCompactNumber(point.parameterValue)}
            </text>
          </g>
        ))}
        <text
          x={width / 2}
          y={height - 4}
          textAnchor="middle"
          fill="rgba(148,163,184,.62)"
          fontSize="11"
        >
          {parameterName}
        </text>
      </svg>
    </div>
  );
}

function paddedRange(values: number[]) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const rawRange = Math.max(max - min, Math.max(Math.abs(max), 1) * 0.001);
  const padding = rawRange * 0.08;
  return { min: min - padding, max: max + padding };
}

function valueTicks(min: number, max: number, count: number) {
  return Array.from(
    { length: count },
    (_, index) => max - ((max - min) * index) / Math.max(count - 1, 1),
  );
}

function formatCompactNumber(value: number) {
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 4,
  }).format(value);
}

function formatDateTick(timestamp: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  })
    .format(new Date(timestamp))
    .replaceAll("/", "-");
}

function formatShortDate(value: string) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? formatDateTick(timestamp) : value;
}

function timeframeLabel(value: string) {
  return {
    "5m": "5分钟",
    "15m": "15分钟",
    "1h": "1小时",
    "4h": "4小时",
  }[value] ?? value;
}

function formatSignedPercentValue(value: number) {
  const formatted = `${Math.abs(value * 100).toFixed(2)}%`;
  if (value > 0) {
    return `+${formatted}`;
  }
  if (value < 0) {
    return `-${formatted}`;
  }
  return "0.00%";
}
