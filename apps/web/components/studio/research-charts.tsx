"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { apiFetch, RunBundleChart, Trial } from "@/lib/api";
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
  bundleId,
  trials,
}: {
  bundleId: string | null;
  trials: Trial[];
}) {
  const chart = useQuery({
    queryKey: ["run-bundle-chart", bundleId],
    queryFn: () =>
      apiFetch<RunBundleChart>(
        `/api/run-bundles/${encodeURIComponent(bundleId ?? "")}/chart-series?max_points=500`,
      ),
    enabled: Boolean(bundleId),
    retry: false,
  });
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const series = useMemo(
    () =>
      (chart.data?.series ?? []).filter(
        (item) => visible[item.series_id] !== false,
      ),
    [chart.data?.series, visible],
  );

  return (
    <div className="space-y-5">
      {chart.data?.available && series.length ? (
        <>
          <div className="flex flex-wrap gap-2">
            {(chart.data.series ?? []).map((item) => (
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
                {item.label}
              </label>
            ))}
          </div>
          <SvgLineChart
            title="归一化资金曲线"
            series={series.map((item) => ({
              id: item.series_id,
              label: item.label,
              values: item.points.map((point) => point.normalized_equity),
            }))}
            baseline={1}
          />
          <SvgLineChart
            title="回撤"
            series={series.map((item) => ({
              id: item.series_id,
              label: item.label,
              values: item.points.map((point) => point.drawdown),
            }))}
            baseline={0}
          />
          <TechnicalDetails label="曲线来源与限制">
            {chart.data.limitations.map((item) => (
              <p key={item}>{item}</p>
            ))}
            {chart.data.series.map((item) => (
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
          {chart.data?.reason ??
            "当前研究结果没有可读取的资金曲线。系统不会用参数指标伪造 Top Trial 曲线。"}
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

function SvgLineChart({
  title,
  series,
  baseline,
}: {
  title: string;
  series: Array<{ id: string; label: string; values: number[] }>;
  baseline: number;
}) {
  const width = 760;
  const height = 220;
  const padding = 20;
  const all = series.flatMap((item) => item.values);
  const min = Math.min(...all, baseline);
  const max = Math.max(...all, baseline);
  const range = Math.max(max - min, 0.000001);
  const colors = ["#3dd6b0", "#7dd3fc", "#fbbf24", "#fda4af"];
  const y = (value: number) =>
    padding + ((max - value) / range) * (height - padding * 2);
  return (
    <div className="overflow-hidden rounded-xl border border-white/10 bg-black/10 p-3">
      <div className="mb-2 text-sm font-medium text-slate-200">{title}</div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={title}
        className="h-auto w-full"
      >
        <line
          x1={padding}
          x2={width - padding}
          y1={y(baseline)}
          y2={y(baseline)}
          stroke="rgba(148,163,184,.28)"
          strokeDasharray="5 5"
        />
        {series.map((item, index) => {
          const points = item.values
            .map((value, pointIndex) => {
              const x =
                padding +
                (pointIndex / Math.max(item.values.length - 1, 1)) *
                  (width - padding * 2);
              return `${x},${y(value)}`;
            })
            .join(" ");
          return (
            <polyline
              key={item.id}
              points={points}
              fill="none"
              stroke={colors[index % colors.length]}
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          );
        })}
      </svg>
    </div>
  );
}
