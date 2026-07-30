"use client";

import { useState } from "react";

export function Section({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-white/10 py-6 first:border-t-0 first:pt-0">
      {eyebrow ? (
        <div className="mb-2 text-[11px] font-semibold tracking-[0.16em] text-emerald-300">
          {eyebrow}
        </div>
      ) : null}
      <h2 className="mb-4 text-lg font-semibold tracking-tight text-slate-100">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function RailPanel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-white/10 bg-[#0a151e]/90 p-4">
      <h2 className="mb-3 text-xs font-semibold tracking-[0.12em] text-slate-400">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-white/[0.06] py-2 first:pt-0 last:border-0 last:pb-0">
      <span className="text-slate-500">{label}</span>
      <span className="max-w-[65%] text-right text-slate-200">{value}</span>
    </div>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-white/15 p-4 text-sm leading-6 text-slate-500">
      {children}
    </div>
  );
}

export function TechnicalDetails({
  children,
  label = "查看技术详情",
}: {
  children: React.ReactNode;
  label?: string;
}) {
  return (
    <details className="mt-3 rounded-lg border border-white/[0.08] bg-black/10 p-3 text-xs text-slate-500">
      <summary className="cursor-pointer select-none text-slate-400">{label}</summary>
      <div className="mt-3 space-y-2 break-words">{children}</div>
    </details>
  );
}

export function TechnicalId({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const displayValue =
    value.length > 28 ? `${value.slice(0, 14)}…${value.slice(-8)}` : value;
  return (
    <div className="flex items-start justify-between gap-3">
      <span>{label}</span>
      <button
        type="button"
        onClick={async () => {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        }}
        aria-label={`复制${label}`}
        title={value}
        className="max-w-[70%] truncate text-right text-slate-300 underline decoration-white/20 underline-offset-2"
      >
        {copied ? "已复制" : displayValue}
      </button>
    </div>
  );
}

export function StatusBadge({
  value,
  tone = "neutral",
}: {
  value: string;
  tone?: "positive" | "warning" | "danger" | "neutral";
}) {
  const styles = {
    positive: "border-emerald-300/25 bg-emerald-300/10 text-emerald-100",
    warning: "border-amber-300/25 bg-amber-300/10 text-amber-100",
    danger: "border-rose-300/25 bg-rose-300/10 text-rose-100",
    neutral: "border-white/10 bg-white/[0.04] text-slate-300",
  };
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs ${styles[tone]}`}>
      {value}
    </span>
  );
}
