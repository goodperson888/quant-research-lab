"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch, Job } from "@/lib/api";

export function JobList() {
  const query = useQuery({ queryKey: ["jobs"], queryFn: () => apiFetch<Job[]>("/api/jobs") });
  if (query.isPending) return <div className="text-sm text-slate-500">读取Job Registry…</div>;
  if (query.isError)
    return <div className="text-sm text-rose-300">无法读取API：{query.error.message}</div>;
  if (!query.data.length)
    return (
      <div className="rounded-2xl border border-dashed border-white/15 p-8 text-center text-sm text-slate-500">
        尚无Job。API只负责排队；阶段0 Worker没有注册回测处理器，不会伪造成功结果。
      </div>
    );
  return (
    <div className="overflow-hidden rounded-2xl border border-white/10">
      {query.data.map((job) => (
        <div key={job.id} className="grid gap-2 border-b border-white/10 bg-white/[0.025] p-4 last:border-0 md:grid-cols-[1fr_140px_160px]">
          <div>
            <div className="font-medium">{job.job_type}</div>
            <div className="mt-1 text-xs text-slate-600">{job.id}</div>
          </div>
          <div className="text-sm text-amber-200">{job.status}</div>
          <div className="text-xs text-slate-500">{new Date(job.created_at).toLocaleString()}</div>
        </div>
      ))}
    </div>
  );
}
