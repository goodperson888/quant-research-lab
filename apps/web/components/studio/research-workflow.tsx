"use client";

export type WorkflowStage =
  | "overview"
  | "strategy"
  | "screening"
  | "diagnosis"
  | "validation"
  | "conclusion";

export type WorkflowStageStatus =
  | "pending"
  | "active"
  | "completed"
  | "stopped"
  | "locked";

export type WorkflowStageItem = {
  id: Exclude<WorkflowStage, "overview">;
  label: string;
  description: string;
  status: WorkflowStageStatus;
  statusLabel: string;
};

export function WorkflowNavigation({
  activeStage,
  stages,
  onChange,
}: {
  activeStage: WorkflowStage;
  stages: WorkflowStageItem[];
  onChange: (stage: WorkflowStage) => void;
}) {
  const options = [
    { id: "overview" as const, label: "总览" },
    ...stages.map((stage) => ({
      id: stage.id,
      label: stage.label,
    })),
  ];
  return (
    <section className="mt-5 rounded-2xl border border-white/10 bg-[#0a151e]/80 p-3 md:p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-200">研究流程</div>
          <div className="mt-1 text-xs leading-5 text-slate-500">
            选择阶段只改变页面视图，不会启动任务、跳过门禁或自动批准结果。
          </div>
        </div>
        <button
          type="button"
          onClick={() => onChange("overview")}
          className={
            activeStage === "overview"
              ? "hidden min-h-11 cursor-pointer rounded-lg border border-emerald-300/30 bg-emerald-300/10 px-3 text-xs font-medium text-emerald-100 md:inline-flex md:items-center"
              : "hidden min-h-11 cursor-pointer rounded-lg border border-white/10 px-3 text-xs text-slate-400 transition hover:border-white/20 hover:text-slate-200 md:inline-flex md:items-center"
          }
        >
          总览
        </button>
      </div>

      <label className="sr-only" htmlFor="workflow-stage-select">
        当前研究阶段
      </label>
      <select
        id="workflow-stage-select"
        value={activeStage}
        onChange={(event) =>
          onChange(event.target.value as WorkflowStage)
        }
        className="w-full rounded-xl border border-white/10 bg-[#071017] px-3 py-3 text-sm text-slate-200 md:hidden"
      >
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {option.label}
          </option>
        ))}
      </select>

      <ol className="hidden grid-cols-5 gap-2 md:grid">
        {stages.map((stage, index) => {
          const active = activeStage === stage.id;
          return (
            <li key={stage.id} className="relative min-w-0">
              {index < stages.length - 1 ? (
                <span
                  aria-hidden="true"
                  className="absolute left-[calc(50%+22px)] right-[-10px] top-[21px] h-px bg-white/10"
                />
              ) : null}
              <button
                type="button"
                onClick={() => onChange(stage.id)}
                aria-current={active ? "step" : undefined}
                className={
                  active
                    ? "relative z-[1] min-h-[104px] w-full cursor-pointer rounded-xl border border-sky-300/30 bg-sky-300/10 p-3 text-left transition"
                    : "relative z-[1] min-h-[104px] w-full cursor-pointer rounded-xl border border-white/[0.08] bg-[#09131b] p-3 text-left transition hover:border-white/20 hover:bg-white/[0.03]"
                }
              >
                <span className="flex items-start justify-between gap-2">
                  <span
                    className={
                      active
                        ? "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-sky-300/40 bg-sky-300/10 text-xs font-semibold text-sky-100"
                        : `flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${statusNumberClass(
                            stage.status,
                          )}`
                    }
                  >
                    {index + 1}
                  </span>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-[10px] ${statusBadgeClass(
                      stage.status,
                    )}`}
                  >
                    {stage.statusLabel}
                  </span>
                </span>
                <span
                  className={
                    active
                      ? "mt-3 block text-sm font-medium text-sky-100"
                      : "mt-3 block text-sm font-medium text-slate-300"
                  }
                >
                  {stage.label}
                </span>
                <span className="mt-1 block truncate text-[11px] text-slate-500">
                  {stage.description}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function StageTabs<T extends string>({
  label,
  active,
  items,
  onChange,
}: {
  label: string;
  active: T;
  items: Array<{
    id: T;
    label: string;
    description?: string;
    badge?: string;
  }>;
  onChange: (view: T) => void;
}) {
  return (
    <div
      className="mb-4 flex max-w-full gap-2 overflow-x-auto pb-1"
      role="tablist"
      aria-label={label}
    >
      {items.map((item) => {
        const selected = active === item.id;
        return (
          <button
            key={item.id}
            id={`${label}-${item.id}`}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(item.id)}
            className={
              selected
                ? "min-h-11 shrink-0 cursor-pointer rounded-xl border border-sky-300/30 bg-sky-300/10 px-3 py-2 text-left text-sky-100 transition"
                : "min-h-11 shrink-0 cursor-pointer rounded-xl border border-white/10 px-3 py-2 text-left text-slate-400 transition hover:border-white/20 hover:bg-white/[0.03] hover:text-slate-200"
            }
          >
            <span className="block text-xs font-medium">{item.label}</span>
            {item.description ? (
              <span className="mt-0.5 block text-[10px] text-slate-500">
                {item.description}
              </span>
            ) : null}
            {item.badge ? (
              <span className="mt-1 block text-[10px] text-slate-500">
                {item.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function StageContainer({
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
    <section className="min-w-0 rounded-2xl border border-white/10 bg-[#0a151e]/70 p-4 md:p-6">
      <div className="mb-5 border-b border-white/[0.07] pb-4">
        <div className="text-[11px] font-semibold tracking-[0.16em] text-emerald-300">
          {eyebrow}
        </div>
        <h2 className="mt-2 text-xl font-semibold tracking-tight text-slate-100">
          {title}
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
          {description}
        </p>
      </div>
      {children}
    </section>
  );
}

function statusNumberClass(status: WorkflowStageStatus) {
  if (status === "completed") {
    return "border-emerald-300/30 bg-emerald-300/10 text-emerald-100";
  }
  if (status === "active") {
    return "border-sky-300/30 bg-sky-300/10 text-sky-100";
  }
  if (status === "stopped") {
    return "border-rose-300/30 bg-rose-300/10 text-rose-100";
  }
  if (status === "locked") {
    return "border-white/[0.08] bg-white/[0.03] text-slate-600";
  }
  return "border-white/10 bg-white/[0.03] text-slate-400";
}

function statusBadgeClass(status: WorkflowStageStatus) {
  if (status === "completed") {
    return "border-emerald-300/20 bg-emerald-300/[0.07] text-emerald-200";
  }
  if (status === "active") {
    return "border-sky-300/20 bg-sky-300/[0.07] text-sky-200";
  }
  if (status === "stopped") {
    return "border-rose-300/20 bg-rose-300/[0.07] text-rose-200";
  }
  if (status === "locked") {
    return "border-white/[0.06] bg-white/[0.02] text-slate-600";
  }
  return "border-white/10 bg-white/[0.03] text-slate-500";
}
