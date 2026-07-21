"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

type DataSummary = {
  available: boolean;
  market_profile?: string;
  source?: { effective_source?: string };
  range?: {
    start_utc_inclusive: string;
    end_utc_exclusive: string;
    complete_utc_days: number;
  };
  datasets?: Record<string, { rows: number; first_timestamp: string; last_timestamp: string }>;
  quality_gaps?: Array<{ dataset: string; timeframe: string; missing_intervals: number }>;
  research_limit?: string;
};

export function DataSummaryPanel() {
  const query = useQuery({
    queryKey: ["data-summary"],
    queryFn: () => apiFetch<DataSummary>("/api/data/summary"),
  });
  if (query.isPending) return <div className="text-sm text-slate-500">读取数据摘要…</div>;
  if (query.isError)
    return <div className="text-sm text-rose-300">无法读取API：{query.error.message}</div>;
  if (!query.data.available)
    return <div className="text-sm text-amber-200">当前没有可用的提交摘要。</div>;
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card label="Market Profile" value={query.data.market_profile ?? "—"} />
        <Card label="实际来源" value={query.data.source?.effective_source ?? "—"} />
        <Card label="完整UTC日" value={String(query.data.range?.complete_utc_days ?? "—")} />
        <Card label="质量缺口组" value={String(query.data.quality_gaps?.length ?? 0)} />
      </div>
      <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-5">
        <div className="mb-4 text-sm font-semibold">数据集</div>
        <div className="grid gap-2">
          {Object.entries(query.data.datasets ?? {}).map(([name, dataset]) => (
            <div key={name} className="grid gap-2 border-b border-white/[0.06] py-2 text-sm last:border-0 sm:grid-cols-[1fr_100px_2fr]">
              <span className="text-slate-300">{name}</span>
              <span className="text-slate-500">{dataset.rows} rows</span>
              <span className="text-xs text-slate-600">{dataset.first_timestamp} → {dataset.last_timestamp}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="rounded-xl border border-amber-300/15 bg-amber-300/[0.05] p-4 text-sm leading-6 text-amber-100/80">
        {query.data.research_limit}
      </div>
    </div>
  );
}

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-2 break-words text-sm text-slate-200">{value}</div>
    </div>
  );
}
