"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { EmptyState, StatusBadge } from "@/components/studio/studio-primitives";

type DataSummary = {
  available: boolean;
  market_profile?: string;
  source?: { effective_source?: string };
  range?: {
    start_utc_inclusive: string;
    end_utc_exclusive: string;
    complete_utc_days: number;
  };
  datasets?: Record<
    string,
    { rows: number; first_timestamp: string; last_timestamp: string }
  >;
  quality_gaps?: Array<{
    dataset: string;
    timeframe: string;
    missing_intervals: number;
  }>;
  research_limit?: string;
};

export function DataSummaryPanel() {
  const query = useQuery({
    queryKey: ["data-summary"],
    queryFn: () => apiFetch<DataSummary>("/api/data/summary"),
  });
  if (query.isPending) {
    return <div className="text-sm text-slate-500">正在读取市场数据……</div>;
  }
  if (query.isError) {
    return (
      <div className="rounded-xl border border-rose-300/20 bg-rose-300/[0.06] p-4 text-sm text-rose-100">
        无法读取市场数据：{query.error.message}
      </div>
    );
  }
  if (!query.data.available) {
    return (
      <EmptyState>
        当前没有可读取的数据摘要。请先完成数据下载和数据目录构建。
      </EmptyState>
    );
  }
  const datasets = Object.entries(query.data.datasets ?? {});
  const gaps = query.data.quality_gaps ?? [];
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card label="市场范围" value={marketProfileLabel(query.data.market_profile)} />
        <Card
          label="实际数据源"
          value={sourceLabel(query.data.source?.effective_source)}
        />
        <Card
          label="完整自然日"
          value={`${query.data.range?.complete_utc_days ?? "—"} 天`}
        />
        <Card
          label="质量状态"
          value={gaps.length ? `${gaps.length} 组缺口` : "未发现缺口"}
          tone={gaps.length ? "warning" : "positive"}
        />
      </div>
      {query.data.range ? (
        <section className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
          <div className="text-xs text-slate-500">当前可回测时间范围</div>
          <div className="mt-2 text-sm tabular-nums text-slate-200">
            {formatDate(query.data.range.start_utc_inclusive)} —{" "}
            {formatDate(query.data.range.end_utc_exclusive)}
          </div>
          <div className="mt-2 text-xs leading-5 text-slate-500">
            所有自动回测使用带版本号的数据快照；更新数据不会静默覆盖旧实验的数据版本。
          </div>
        </section>
      ) : null}
      <section className="rounded-2xl border border-white/10 bg-white/[0.025] p-4 md:p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">已登记数据集</h2>
            <p className="mt-1 text-xs text-slate-500">
              行数用于判断数据量，首尾时间用于确认覆盖范围。
            </p>
          </div>
          <StatusBadge
            value={`${datasets.length} 个数据集`}
            tone="neutral"
          />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] text-left text-xs tabular-nums">
            <thead className="text-slate-500">
              <tr>
                <th className="py-2 pr-4 font-medium">数据集</th>
                <th className="py-2 pr-4 font-medium">周期</th>
                <th className="py-2 pr-4 font-medium">行数</th>
                <th className="py-2 font-medium">覆盖范围</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map(([name, dataset]) => {
                const identity = datasetIdentity(name);
                return (
                  <tr
                    key={name}
                    className="border-t border-white/[0.06] text-slate-300"
                  >
                    <td className="py-3 pr-4">{identity.label}</td>
                    <td className="py-3 pr-4">{identity.timeframe}</td>
                    <td className="py-3 pr-4">
                      {dataset.rows.toLocaleString("zh-CN")}
                    </td>
                    <td className="py-3 text-slate-400">
                      {formatDate(dataset.first_timestamp)} —{" "}
                      {formatDate(dataset.last_timestamp)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      {gaps.length ? (
        <section className="rounded-xl border border-amber-300/15 bg-amber-300/[0.05] p-4">
          <h2 className="text-sm font-medium text-amber-100">数据质量缺口</h2>
          <div className="mt-3 space-y-2">
            {gaps.map((gap) => (
              <div
                key={`${gap.dataset}:${gap.timeframe}`}
                className="flex flex-wrap items-center justify-between gap-2 border-t border-amber-100/10 pt-2 text-xs first:border-0 first:pt-0"
              >
                <span className="text-amber-100/80">
                  {datasetLabel(gap.dataset)} · {timeframeLabel(gap.timeframe)}
                </span>
                <span className="tabular-nums text-amber-200">
                  缺少 {gap.missing_intervals.toLocaleString("zh-CN")} 个区间
                </span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      {query.data.research_limit ? (
        <div className="rounded-xl border border-amber-300/15 bg-amber-300/[0.05] p-4 text-sm leading-6 text-amber-100/80">
          {researchLimitLabel(
            query.data.research_limit,
            query.data.range?.complete_utc_days,
          )}
        </div>
      ) : null}
    </div>
  );
}

function Card({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "positive" | "warning" | "neutral";
}) {
  const color = {
    positive: "text-emerald-200",
    warning: "text-amber-200",
    neutral: "text-slate-200",
  }[tone];
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-2 break-words text-sm ${color}`}>{value}</div>
    </div>
  );
}

function datasetIdentity(name: string) {
  const timeframe = ["5m", "15m", "1h", "4h"].find((item) =>
    name.includes(item),
  );
  const dataset = name
    .replace(timeframe ?? "", "")
    .replaceAll(/[:/_-]+/g, " ")
    .trim();
  return {
    label: datasetLabel(dataset || name),
    timeframe: timeframeLabel(timeframe),
  };
}

function datasetLabel(value: string) {
  const normalized = value.toLowerCase();
  if (normalized.includes("ohlcv")) return "永续合约K线";
  if (normalized.includes("funding")) return "资金费率";
  if (normalized.includes("mark")) return "标记价格";
  if (normalized.includes("index")) return "指数价格";
  if (normalized.includes("open_interest")) return "持仓量指标";
  if (normalized.includes("liquidation")) return "强平数据";
  if (normalized.includes("trade")) return "成交数据";
  return value;
}

function timeframeLabel(value: string | undefined) {
  return {
    "5m": "5分钟",
    "15m": "15分钟",
    "1h": "1小时",
    "4h": "4小时",
  }[value ?? ""] ?? "非K线";
}

function marketProfileLabel(value: string | undefined) {
  if (!value) return "—";
  if (value.includes("crypto_perpetual")) return "加密货币永续合约";
  return value;
}

function sourceLabel(value: string | undefined) {
  if (!value) return "—";
  if (value.includes("binance")) return "Binance 官方历史数据";
  if (value.includes("okx")) return "OKX";
  return value;
}

function researchLimitLabel(value: string, completeDays: number | undefined) {
  if (value.includes("complete UTC days improve regime coverage")) {
    return `当前 ${completeDays ?? "—"} 个完整 UTC 自然日有助于扩大行情覆盖，但仍不能证明未来盈利，也不能代表已经覆盖了可重复的完整市场周期。`;
  }
  return value;
}

function formatDate(value: string) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Date(timestamp).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
