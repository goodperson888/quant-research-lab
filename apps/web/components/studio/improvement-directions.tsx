import { ImprovementDirection } from "@/lib/api";
import { cnStatus } from "@/components/studio/studio-labels";
import { EmptyState, TechnicalDetails, TechnicalId } from "@/components/studio/studio-primitives";

export function ImprovementDirections({
  directions,
  providerConfigured,
  pending,
  onSubmit,
  onApprove,
  onBudgetChange,
}: {
  directions: ImprovementDirection[];
  providerConfigured: boolean;
  pending: boolean;
  onSubmit: (proposalId: string) => void;
  onApprove: (proposalId: string) => void;
  onBudgetChange: (proposalId: string, trials: number, minutes: number) => void;
}) {
  if (!directions.length) {
    return (
      <EmptyState>
        {providerConfigured
          ? "尚无结构化改进方向。"
          : "网页模型未配置，不伪造 AI 建议。人工或本地研究助手可通过同一接口创建结构化方向。"}
      </EmptyState>
    );
  }
  return (
    <div className="grid min-w-0 gap-3 lg:grid-cols-3">
      {directions.slice(0, 3).map((direction) => (
        <article
          key={direction.id}
          className="min-w-0 rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs"
        >
          <div className="font-medium leading-5 text-slate-100">
            {direction.hypothesis}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-slate-400">
            <span>{direction.estimated_trials ?? "—"} 个参数方案</span>
            <span>{direction.estimated_minutes ?? "—"} 分钟</span>
            <span>{direction.parameter_space.length} 个参数</span>
            <span>{direction.evidence_refs.length} 条证据</span>
          </div>
          <div className="mt-2 rounded-lg bg-white/[0.03] p-2 text-slate-500">
            状态：{cnStatus(direction.status)}
          </div>
          {direction.status === "waiting_approval" ? (
            <button
              type="button"
              onClick={() => onApprove(direction.id)}
              disabled={pending}
              className="mt-2 w-full rounded-lg border border-emerald-300/30 bg-emerald-300/10 px-2 py-2 text-emerald-100 disabled:opacity-40"
            >
              批准这个方向与预算
            </button>
          ) : null}
          {direction.status === "draft" ? (
            <button
              type="button"
              onClick={() => onSubmit(direction.id)}
              disabled={pending}
              className="mt-2 w-full rounded-lg border border-sky-300/25 bg-sky-300/10 px-2 py-2 text-sky-100 disabled:opacity-40"
            >
              提交这个方向审批
            </button>
          ) : null}
          {direction.status === "draft" || direction.status === "waiting_approval" ? (
            <form
              className="mt-2 grid grid-cols-[1fr_1fr_auto] gap-1"
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                onBudgetChange(
                  direction.id,
                  Number(form.get("trials")),
                  Number(form.get("minutes")),
                );
              }}
            >
              <input
                name="trials"
                type="number"
                min={1}
                defaultValue={direction.estimated_trials ?? 20}
                aria-label="参数方案预算"
                className="min-w-0 rounded-md border border-white/10 bg-[#071017] px-2 py-1.5 text-slate-200"
              />
              <input
                name="minutes"
                type="number"
                min={1}
                defaultValue={direction.estimated_minutes ?? 45}
                aria-label="计算时间预算（分钟）"
                className="min-w-0 rounded-md border border-white/10 bg-[#071017] px-2 py-1.5 text-slate-200"
              />
              <button
                type="submit"
                className="rounded-md border border-white/10 px-2 text-slate-300"
              >
                调整
              </button>
            </form>
          ) : null}
          <TechnicalDetails>
            <TechnicalId label="改进方案对象" value={direction.id} />
          </TechnicalDetails>
        </article>
      ))}
    </div>
  );
}
