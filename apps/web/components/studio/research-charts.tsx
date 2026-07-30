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

export function ResearchCharts({
  bundles,
  trials,
}: {
  bundles: RunBundle[];
  trials: Trial[];
}) {
  const chartQueries = useQueries({
    queries: bundles.slice(0, 8).map((bundle) => ({
      queryKey: ["run-bundle-chart", bundle.bundle_id],
      queryFn: () =>
        apiFetch<RunBundleChart>(
          `/api/run-bundles/${encodeURIComponent(bundle.bundle_id)}/chart-series?max_points=500`,
        ),
      retry: false,
    })),
  });
  const selectedChartIndex = chartQueries.findIndex(
    (query) => query.data?.available && query.data.series.length,
  );
  const chart =
    selectedChartIndex >= 0 ? chartQueries[selectedChartIndex].data : undefined;
  const selectedBundle =
    selectedChartIndex >= 0 ? bundles[selectedChartIndex] : undefined;
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
      <div>
        <div className="mb-1 text-sm font-medium text-slate-200">
          资金曲线与回撤
        </div>
        <div className="text-xs leading-5 text-slate-500">
          日期使用 UTC。系统会叠加当前策略已保存的试跑、快速初筛与验证曲线，并用同期 ETH 永续价格帮助判断收益发生在哪种行情。
        </div>
      </div>
      {chart?.available && series.length ? (
        <>
          {selectedBundle && selectedChartIndex > 0 ? (
            <div className="rounded-lg border border-sky-300/15 bg-sky-300/[0.07] px-3 py-2 text-xs leading-5 text-sky-100">
              最新报告未保存资金曲线，已自动采用最近一份有真实曲线的研究结果。
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {coloredSeries.map((item) => (
              <label
                key={item.series_id}
                className="flex items-center gap-2 rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-300"
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
          {chart.market_series ? (
            <SvgMarketComparisonChart
              market={chart.market_series}
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
            <SvgLineChart
              title="策略资金曲线"
              series={series.map((item) => ({
                id: item.series_id,
                label: item.label,
                color: item.color,
                points: item.points.map((point) => ({
                  t: point.t,
                  value: point.normalized_equity,
                })),
              }))}
              baseline={1}
              formatValue={(value) => formatSignedPercentValue(value - 1)}
            />
          )}
          {!chart.market_series && chart.market_reason ? (
            <div className="text-xs leading-5 text-amber-100/80">
              行情对照暂不可用：{chart.market_reason}
            </div>
          ) : null}
          <SvgLineChart
            title="策略回撤"
            series={series.map((item) => ({
              id: item.series_id,
              label: item.label,
              color: item.color,
              points: item.points.map((point) => ({
                t: point.t,
                value: point.drawdown,
              })),
            }))}
            baseline={0}
            formatValue={formatSignedPercentValue}
          />
          <TechnicalDetails label="曲线来源与限制">
            {chart.limitations.map((item) => (
              <p key={item}>{item}</p>
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
      <TrialMetricComparison trials={trials} />
    </div>
  );
}

function TrialMetricComparison({ trials }: { trials: Trial[] }) {
  const succeeded = trials.filter((item) => item.status === "succeeded").slice(0, 8);
  if (!succeeded.length) {
    return (
      <EmptyState>
        尚无成功参数方案可比较。批量任务完成后，这里会先显示净收益、回撤和交易数的相对差异。
      </EmptyState>
    );
  }
  const values = succeeded.map(
    (item) => finiteOrZero(item.metrics.validation_net_return),
  );
  const scale = Math.max(...values.map((value) => Math.abs(value)), 0.001);
  return (
    <div>
      <div className="mb-3 text-sm font-medium text-slate-200">
        多参数方案验证净收益
      </div>
      <div className="space-y-2">
        {succeeded.map((trial) => {
          const value = finiteOrZero(trial.metrics.validation_net_return);
          return (
            <div key={trial.id} className="grid grid-cols-[minmax(0,1fr)_90px] gap-3 text-xs">
              <div>
                <div className="truncate text-slate-300">
                  {formatTrialParameters(trial.parameters)}
                </div>
                <div className="mt-1 h-2 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className={value >= 0 ? "h-full bg-emerald-300/70" : "ml-auto h-full bg-rose-300/70"}
                    style={{ width: `${Math.max((Math.abs(value) / scale) * 100, 2)}%` }}
                  />
                </div>
              </div>
              <div className={value >= 0 ? "text-right text-emerald-200" : "text-right text-rose-200"}>
                {formatPercent(value)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function finiteOrZero(value: number | undefined) {
  return Number.isFinite(value) ? (value ?? 0) : 0;
}

const CHART_COLORS = ["#3dd6b0", "#7dd3fc", "#fbbf24", "#fda4af", "#c4b5fd", "#fb923c"];

type TimeValuePoint = {
  t: string;
  value: number;
};

type TimeSeriesLine = {
  id: string;
  label: string;
  color: string;
  points: TimeValuePoint[];
};

function SvgMarketComparisonChart({
  market,
  series,
}: {
  market: RunBundleChart["market_series"] extends infer T ? NonNullable<T> : never;
  series: TimeSeriesLine[];
}) {
  const width = 860;
  const height = 310;
  const padding = { top: 24, right: 72, bottom: 44, left: 58 };
  const timeValues = [
    ...market.points.map((point) => Date.parse(point.t)),
    ...series.flatMap((item) => item.points.map((point) => Date.parse(point.t))),
  ].filter(Number.isFinite);
  const start = Math.min(...timeValues);
  const end = Math.max(...timeValues);
  const equityValues = series.flatMap((item) =>
    item.points.map((point) => point.value),
  );
  const equityScale = paddedRange([...equityValues, 1]);
  const marketScale = paddedRange(market.points.map((point) => point.value));
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const x = (timestamp: number) =>
    padding.left +
    ((timestamp - start) / Math.max(end - start, 1)) * plotWidth;
  const equityY = (value: number) =>
    padding.top +
    ((equityScale.max - value) /
      Math.max(equityScale.max - equityScale.min, 0.000001)) *
      plotHeight;
  const marketY = (value: number) =>
    padding.top +
    ((marketScale.max - value) /
      Math.max(marketScale.max - marketScale.min, 0.000001)) *
      plotHeight;
  const dateTicks = timeTicks(start, end, 5);
  const horizontalTicks = valueTicks(equityScale.min, equityScale.max, 4);

  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-black/10 p-3">
      <div className="mb-1 text-sm font-medium text-slate-200">
        ETH 行情与策略资金曲线
      </div>
      <div className="mb-3 text-xs leading-5 text-slate-500">
        左轴为策略累计收益，右轴为 ETH 永续价格；两者共用同一日期轴。
      </div>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 bg-slate-400" />
          {market.label}
        </span>
        {series.map((item) => (
          <span key={item.id} className="flex items-center gap-1.5">
            <span
              className="h-0.5 w-4"
              style={{ backgroundColor: item.color }}
            />
            {item.label}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="ETH 行情与策略资金曲线"
        className="h-auto w-full"
      >
        {horizontalTicks.map((value) => (
          <g key={value}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={equityY(value)}
              y2={equityY(value)}
              stroke="rgba(148,163,184,.12)"
            />
            <text
              x={padding.left - 8}
              y={equityY(value) + 4}
              textAnchor="end"
              fill="rgba(148,163,184,.72)"
              fontSize="11"
            >
              {formatSignedPercentValue(value - 1)}
            </text>
          </g>
        ))}
        <line
          x1={padding.left}
          x2={width - padding.right}
          y1={equityY(1)}
          y2={equityY(1)}
          stroke="rgba(226,232,240,.3)"
          strokeDasharray="5 5"
        />
        {dateTicks.map((timestamp, index) => (
          <g key={timestamp}>
            <line
              x1={x(timestamp)}
              x2={x(timestamp)}
              y1={padding.top}
              y2={height - padding.bottom}
              stroke="rgba(148,163,184,.08)"
            />
            <text
              x={x(timestamp)}
              y={height - 14}
              textAnchor={
                index === 0
                  ? "start"
                  : index === dateTicks.length - 1
                    ? "end"
                    : "middle"
              }
              fill="rgba(148,163,184,.72)"
              fontSize="11"
            >
              {formatDateTick(timestamp)}
            </text>
          </g>
        ))}
        <polyline
          points={market.points
            .map((point) => `${x(Date.parse(point.t))},${marketY(point.value)}`)
            .join(" ")}
          fill="none"
          stroke="#94a3b8"
          strokeOpacity="0.9"
          strokeWidth="1.7"
          vectorEffect="non-scaling-stroke"
        />
        {series.map((item) => (
          <polyline
            key={item.id}
            points={item.points
              .map((point) => `${x(Date.parse(point.t))},${equityY(point.value)}`)
              .join(" ")}
            fill="none"
            stroke={item.color}
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {valueTicks(marketScale.min, marketScale.max, 4).map((value) => (
          <text
            key={value}
            x={width - padding.right + 8}
            y={marketY(value) + 4}
            textAnchor="start"
            fill="rgba(148,163,184,.72)"
            fontSize="11"
          >
            {formatPrice(value)}
          </text>
        ))}
      </svg>
    </div>
  );
}

function SvgLineChart({
  title,
  series,
  baseline,
  formatValue,
}: {
  title: string;
  series: TimeSeriesLine[];
  baseline: number;
  formatValue: (value: number) => string;
}) {
  const width = 860;
  const height = 280;
  const padding = { top: 22, right: 20, bottom: 44, left: 58 };
  const all = series.flatMap((item) => item.points.map((point) => point.value));
  const scale = paddedRange([...all, baseline]);
  const timeValues = series
    .flatMap((item) => item.points.map((point) => Date.parse(point.t)))
    .filter(Number.isFinite);
  const start = Math.min(...timeValues);
  const end = Math.max(...timeValues);
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const x = (timestamp: number) =>
    padding.left +
    ((timestamp - start) / Math.max(end - start, 1)) * plotWidth;
  const y = (value: number) =>
    padding.top +
    ((scale.max - value) / Math.max(scale.max - scale.min, 0.000001)) *
      plotHeight;
  const dateTicks = timeTicks(start, end, 5);
  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-black/10 p-3">
      <div className="mb-2 text-sm font-medium text-slate-200">{title}</div>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
        {series.map((item) => (
          <span key={item.id} className="flex items-center gap-1.5">
            <span
              className="h-0.5 w-4"
              style={{ backgroundColor: item.color }}
            />
            {item.label}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={title}
        className="h-auto w-full"
      >
        {valueTicks(scale.min, scale.max, 4).map((value) => (
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
        {dateTicks.map((timestamp, index) => (
          <g key={timestamp}>
            <line
              x1={x(timestamp)}
              x2={x(timestamp)}
              y1={padding.top}
              y2={height - padding.bottom}
              stroke="rgba(148,163,184,.08)"
            />
            <text
              x={x(timestamp)}
              y={height - 14}
              textAnchor={
                index === 0
                  ? "start"
                  : index === dateTicks.length - 1
                    ? "end"
                    : "middle"
              }
              fill="rgba(148,163,184,.72)"
              fontSize="11"
            >
              {formatDateTick(timestamp)}
            </text>
          </g>
        ))}
        {series.map((item) => {
          const points = item.points
            .map((point) => `${x(Date.parse(point.t))},${y(point.value)}`)
            .join(" ");
          return (
            <polyline
              key={item.id}
              points={points}
              fill="none"
              stroke={item.color}
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          );
        })}
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

function timeTicks(start: number, end: number, count: number) {
  return Array.from(
    { length: count },
    (_, index) => start + ((end - start) * index) / Math.max(count - 1, 1),
  );
}

function valueTicks(min: number, max: number, count: number) {
  return Array.from(
    { length: count },
    (_, index) => max - ((max - min) * index) / Math.max(count - 1, 1),
  );
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

function formatPrice(value: number) {
  return `$${new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: value >= 100 ? 0 : 2,
  }).format(value)}`;
}
