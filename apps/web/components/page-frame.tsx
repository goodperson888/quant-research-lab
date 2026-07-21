import { ProjectStatusStrip } from "@/components/project-status";

export function PageFrame({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen p-5 md:p-8">
      <header className="mb-7 border-b border-white/10 pb-6">
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-300">{eyebrow}</div>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">{description}</p>
        <div className="mt-4">
          <ProjectStatusStrip />
        </div>
      </header>
      {children}
    </div>
  );
}

export function EmptyState({
  title,
  body,
}: {
  title: string;
  body: string;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-white/15 bg-white/[0.025] p-8 text-center">
      <div className="text-base font-medium text-slate-200">{title}</div>
      <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-slate-500">{body}</p>
    </div>
  );
}
