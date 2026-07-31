import {
  ComponentCandidate,
  ComponentEvidence,
  ComponentHypothesis,
} from "@/lib/api";
import {
  cnComponentType,
  cnEvidence,
  cnOutcome,
  cnSource,
} from "@/components/studio/studio-labels";
import {
  EmptyState,
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

export function ComponentLibrary({
  hypotheses,
  candidates,
  archivedCandidates,
  evidence,
  onArchive,
  onRestore,
}: {
  hypotheses: ComponentHypothesis[];
  candidates: ComponentCandidate[];
  archivedCandidates: ComponentCandidate[];
  evidence: ComponentEvidence[];
  onArchive: (candidateId: string) => void;
  onRestore: (candidateId: string) => void;
}) {
  return (
    <div className="space-y-4">
      {hypotheses.length ? (
        <div className="grid gap-3 md:grid-cols-3">
          {hypotheses.slice(0, 3).map((item) => (
            <article
              key={item.id}
              className="border-l-2 border-sky-300/40 bg-white/[0.02] px-4 py-3"
            >
              <div className="text-sm font-medium leading-6 text-slate-100">
                {item.title}
              </div>
              <div className="mt-2 text-xs leading-5 text-slate-400">
                {cnComponentType(item.component_type)} · 建议 {item.suggested_trials} 个方案
              </div>
              <div className="mt-2">
                <StatusBadge value={cnEvidence(item.contamination_status)} tone="warning" />
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-500">
                来源：{cnSource(item.source)}。这不是模型自由生成的交易建议。
              </p>
              <TechnicalDetails>
                <TechnicalId label="方向对象" value={item.id} />
              </TechnicalDetails>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState>
          当前没有规则分析器诊断方向。网页模型未配置时不会伪造 AI 建议。
        </EmptyState>
      )}

      <div>
        <div className="mb-2 text-sm font-medium text-slate-200">
          已归档的策略组件证据
        </div>
        {candidates.length ? (
          <div className="divide-y divide-white/[0.06]">
            {candidates.map((candidate) => {
              const item = evidence.find((entry) => entry.id === candidate.evidence_id);
              return (
                <div
                  key={candidate.id}
                  className="grid gap-3 py-3 md:grid-cols-[minmax(0,1fr)_auto]"
                >
                  <div>
                    <div className="text-sm text-slate-100">{candidate.name}</div>
                    <div className="mt-1 text-xs text-slate-400">
                      {item ? cnComponentType(item.component_type) : "策略组件"} ·{" "}
                      {cnOutcome(candidate.status)} ·{" "}
                      {item ? cnEvidence(item.out_of_sample_status) : "证据待补充"}
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                      这是策略规则的一部分，不等同于可跨市场通用的“因子”。
                    </div>
                    <TechnicalDetails>
                      <TechnicalId label="组件对象" value={candidate.id} />
                      <TechnicalId label="证据对象" value={candidate.evidence_id} />
                    </TechnicalDetails>
                  </div>
                  <button
                    type="button"
                    onClick={() => onArchive(candidate.id)}
                    className="self-start rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-300"
                  >
                    从活跃列表归档
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState>
            失败策略可以保留诊断性组件，但不会自动升级为已验证因子或生产策略。
          </EmptyState>
        )}
      </div>
      {archivedCandidates.length ? (
        <details className="rounded-xl border border-white/[0.08] p-3 text-xs text-slate-500">
          <summary className="cursor-pointer text-slate-400">
            已归档组件（{archivedCandidates.length}）
          </summary>
          <div className="mt-3 space-y-2">
            {archivedCandidates.map((candidate) => (
              <div
                key={candidate.id}
                className="flex items-center justify-between gap-3 border-t border-white/[0.06] pt-2 first:border-0 first:pt-0"
              >
                <span>{candidate.name}</span>
                <button
                  type="button"
                  onClick={() => onRestore(candidate.id)}
                  className="rounded-lg border border-white/10 px-2 py-1.5 text-slate-300"
                >
                  恢复到活跃列表
                </button>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
