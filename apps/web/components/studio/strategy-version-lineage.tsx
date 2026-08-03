import {
  ExperimentPlan,
  ImprovementDirection,
  StrategyVersion,
  Trial,
} from "@/lib/api";
import { cnStatus } from "@/components/studio/studio-labels";
import {
  EmptyState,
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

export function StrategyVersionLineage({
  versions,
  directions,
  plans,
  trials,
}: {
  versions: StrategyVersion[];
  directions: ImprovementDirection[];
  plans: ExperimentPlan[];
  trials: Trial[];
}) {
  const baseline = versions.find((item) => item.status === "baseline");
  if (!baseline) {
    return <EmptyState>冻结 Baseline 后才会建立策略版本关系。</EmptyState>;
  }

  const orderedDirections = [...directions].sort((left, right) =>
    left.created_at.localeCompare(right.created_at),
  );

  return (
    <div className="space-y-3 text-sm">
      <div className="rounded-xl border border-emerald-300/20 bg-emerald-300/[0.05] p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-slate-100">
            冻结基准 v{baseline.version}
          </span>
          <StatusBadge value="冻结且不可覆盖" tone="positive" />
        </div>
        <p className="mt-2 text-xs leading-5 text-slate-400">
          这是原始策略的权威基准。后续研究只创建分支版本，不回写这里。
        </p>
        <TechnicalDetails>
          <TechnicalId label="Baseline 对象" value={baseline.id} />
        </TechnicalDetails>
      </div>

      {orderedDirections.length ? (
        <div className="space-y-3 border-l border-white/15 pl-4">
          {orderedDirections.map((direction, index) => {
            const candidate = versions.find(
              (item) => item.id === direction.candidate_version_id,
            );
            const plan = plans.find(
              (item) => item.proposal_id === direction.id,
            );
            const planTrials = plan
              ? trials.filter(
                  (item) => item.experiment_plan_id === plan.id,
                )
              : [];
            const currentPlanLoaded =
              planTrials.length > 0 ||
              trials.some(
                (item) => item.experiment_plan_id === plan?.id,
              );
            return (
              <article
                key={direction.id}
                className="relative rounded-xl border border-white/10 bg-white/[0.02] p-3 before:absolute before:-left-[1.05rem] before:top-6 before:h-px before:w-4 before:bg-white/15"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-slate-100">
                    改进假设 H{index + 1}
                  </span>
                  <StatusBadge value={cnStatus(direction.status)} />
                </div>
                <p className="mt-2 text-xs leading-5 text-slate-400">
                  {direction.hypothesis}
                </p>
                <div className="mt-3 rounded-lg bg-black/10 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-sky-100">
                      {candidate
                        ? `候选版本 v${candidate.version}`
                        : "候选版本尚未创建"}
                    </span>
                    <span className="text-xs text-slate-500">
                      同一策略的改进版本
                    </span>
                  </div>
                  <div className="mt-2 text-xs text-slate-400">
                    {plan
                      ? currentPlanLoaded
                        ? `${planTrials.length}/${plan.max_trials ?? "—"} 个参数试验已登记`
                        : `已建立 ${plan.max_trials ?? "有限"} 个参数试验的计划`
                      : `${direction.estimated_trials ?? "有限"} 个参数试验等待审批`}
                  </div>
                </div>
                <TechnicalDetails>
                  <TechnicalId label="改进方案对象" value={direction.id} />
                  {candidate ? (
                    <TechnicalId label="Candidate 对象" value={candidate.id} />
                  ) : null}
                  {plan ? (
                    <TechnicalId label="试验计划对象" value={plan.id} />
                  ) : null}
                </TechnicalDetails>
              </article>
            );
          })}
        </div>
      ) : (
        <EmptyState>
          目前只有 Baseline。参数值变化不会另建策略；批准规则改进后才会出现 Candidate 分支。
        </EmptyState>
      )}

      <div className="rounded-lg border border-white/[0.08] px-3 py-2 text-xs leading-5 text-slate-500">
        归类规则：改参数值＝同一候选版本下的参数试验；改一条规则＝同一策略主体的新候选版本；
        核心交易思想、市场或执行逻辑完全变化＝新策略主体。
      </div>
    </div>
  );
}
