"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch, StrategyDraft } from "@/lib/api";

export function StrategyList() {
  const query = useQuery({
    queryKey: ["strategy-drafts"],
    queryFn: () => apiFetch<StrategyDraft[]>("/api/strategy-drafts"),
  });
  if (query.isPending) return <div className="text-sm text-slate-500">读取策略草稿…</div>;
  if (query.isError)
    return <div className="text-sm text-rose-300">无法读取API：{query.error.message}</div>;
  if (!query.data.length)
    return <div className="text-sm text-slate-500">尚无策略。请从 Studio 保存第一份原始策略。</div>;
  return (
    <div className="grid gap-3">
      {query.data.map((draft) => (
        <article key={draft.id} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-medium">{draft.source_name ?? draft.source_type}</div>
            <div className="rounded-full border border-white/10 px-2.5 py-1 text-xs text-slate-400">{draft.status}</div>
          </div>
          <p className="mt-3 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-slate-400">{draft.raw_content}</p>
          <div className="mt-3 text-xs text-slate-600">{draft.id}</div>
        </article>
      ))}
    </div>
  );
}
