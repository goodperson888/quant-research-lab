"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  apiFetch,
  ComponentCandidate,
  ComponentEvidence,
  FactorRegistryItem,
  StrategyDraft,
  StrategyOutcome,
  StrategyVersion,
} from "@/lib/api";
import {
  cnComponentType,
  cnEvidence,
  cnOutcome,
  cnStatus,
  formatSignedPercent,
} from "@/components/studio/studio-labels";
import {
  EmptyState,
  StatusBadge,
  TechnicalDetails,
  TechnicalId,
} from "@/components/studio/studio-primitives";

type AssetView = "strategies" | "components" | "factors";

export function ResearchAssetLibrary() {
  const queryClient = useQueryClient();
  const [view, setView] = useState<AssetView>("strategies");
  const [notice, setNotice] = useState<string | null>(null);
  const drafts = useQuery({
    queryKey: ["asset-library", "strategy-drafts"],
    queryFn: () => apiFetch<StrategyDraft[]>("/api/strategy-drafts"),
  });
  const versions = useQuery({
    queryKey: ["asset-library", "strategy-versions"],
    queryFn: () => apiFetch<StrategyVersion[]>("/api/strategy-versions"),
  });
  const outcomes = useQuery({
    queryKey: ["asset-library", "strategy-outcomes"],
    queryFn: () => apiFetch<StrategyOutcome[]>("/api/strategy-outcomes"),
  });
  const components = useQuery({
    queryKey: ["asset-library", "component-candidates"],
    queryFn: () =>
      apiFetch<ComponentCandidate[]>(
        "/api/component-candidates?include_archived=true",
      ),
  });
  const evidence = useQuery({
    queryKey: ["asset-library", "component-evidence"],
    queryFn: () => apiFetch<ComponentEvidence[]>("/api/component-evidence"),
  });
  const factors = useQuery({
    queryKey: ["asset-library", "factors"],
    queryFn: () => apiFetch<FactorRegistryItem[]>("/api/factors"),
  });
  const promoteFactor = useMutation({
    mutationFn: (candidate: ComponentCandidate) =>
      apiFetch<FactorRegistryItem>(
        `/api/component-candidates/${candidate.id}/promote-factor-candidate`,
        {
          method: "POST",
          body: JSON.stringify({
            subject_id: candidate.id,
            confirmed_by_user: true,
            name: humanComponentName(candidate.name),
          }),
        },
      ),
    onSuccess: () => {
      setNotice(
        "已沉淀为独立因子候选；状态仍是“候选”，不会自动变成已验证因子。",
      );
      queryClient.invalidateQueries({ queryKey: ["asset-library", "factors"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
      setView("factors");
    },
  });
  const archiveFactor = useMutation({
    mutationFn: ({
      factorId,
      restore,
    }: {
      factorId: string;
      restore: boolean;
    }) =>
      apiFetch<FactorRegistryItem>(
        `/api/factors/${factorId}/${restore ? "restore" : "archive"}`,
        {
          method: "POST",
          body: JSON.stringify({
            subject_id: factorId,
            confirmed_by_user: true,
          }),
        },
      ),
    onSuccess: (_result, variables) => {
      setNotice(
        variables.restore
          ? "因子候选已恢复到活跃列表。"
          : "因子候选已软归档；来源、证据和失败记录仍完整保留。",
      );
      queryClient.invalidateQueries({ queryKey: ["asset-library", "factors"] });
      queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    },
  });

  const strategyVersions = versions.data ?? [];
  const strategyOutcomes = outcomes.data ?? [];
  const componentCandidates = components.data ?? [];
  const factorItems = factors.data ?? [];
  const validatedStrategyCount = new Set(
    strategyOutcomes
      .filter((item) => item.outcome_type === "validated")
      .map((item) => item.strategy_version_id),
  ).size;
  const candidateStrategyCount = new Set(
    strategyOutcomes
      .filter((item) => item.outcome_type === "strategy_candidate")
      .map((item) => item.strategy_version_id),
  ).size;
  const componentCandidateCount = componentCandidates.filter(
    (item) => item.status === "component_candidate" && item.archived_at === null,
  ).length;
  const diagnosticComponentCount = componentCandidates.filter(
    (item) =>
      item.status === "diagnostic_improvement" && item.archived_at === null,
  ).length;
  const validatedFactorCount = factorItems.filter(
    (item) => item.status === "validated" || item.status === "production",
  ).length;
  const isPending = [
    drafts,
    versions,
    outcomes,
    components,
    evidence,
    factors,
  ].some((query) => query.isPending);

  const tabs: Array<{
    id: AssetView;
    label: string;
    description: string;
    count: number;
  }> = [
    {
      id: "strategies",
      label: "策略资产",
      description: "原始策略、基准与候选版本",
      count: strategyVersions.length,
    },
    {
      id: "components",
      label: "可复用组件",
      description: "入场、过滤、出场与风控证据",
      count: componentCandidates.length,
    },
    {
      id: "factors",
      label: "独立因子库",
      description: "跨策略复用但按市场隔离验证",
      count: factorItems.length,
    },
  ];

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <AssetMetric
          label="已验证策略"
          value={validatedStrategyCount}
          helper="通过指定市场的完整证据链"
          tone={validatedStrategyCount ? "positive" : "neutral"}
        />
        <AssetMetric
          label="待验证策略"
          value={candidateStrategyCount}
          helper="已正式进入策略候选层级"
          tone={candidateStrategyCount ? "warning" : "neutral"}
        />
        <AssetMetric
          label="组件候选"
          value={componentCandidateCount}
          helper="值得单独继续验证的局部规则"
          tone={componentCandidateCount ? "warning" : "neutral"}
        />
        <AssetMetric
          label="诊断组件"
          value={diagnosticComponentCount}
          helper="保留经验，但证据不足"
          tone="neutral"
        />
        <AssetMetric
          label="已验证因子"
          value={validatedFactorCount}
          helper={`独立因子总数 ${factorItems.length}`}
          tone={validatedFactorCount ? "positive" : "neutral"}
        />
      </div>

      <div className="rounded-xl border border-sky-300/15 bg-sky-300/[0.06] px-4 py-3 text-sm leading-6 text-sky-100/90">
        当前资产严格分层：策略候选、组件候选和诊断性改进都不会自动算作“有效策略”或“已验证因子”。
        只有通过样本外、成本、敏感性和压力证据后，才会进入已验证状态。
      </div>
      {notice ? (
        <div className="rounded-xl border border-emerald-300/20 bg-emerald-300/[0.06] px-4 py-3 text-sm leading-6 text-emerald-100">
          {notice}
        </div>
      ) : null}

      <div
        className="grid gap-2 md:grid-cols-3"
        role="tablist"
        aria-label="研究资产分类"
      >
        {tabs.map((tab) => {
          const active = view === tab.id;
          return (
            <button
              key={tab.id}
              id={`asset-tab-${tab.id}`}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={`asset-panel-${tab.id}`}
              onClick={() => setView(tab.id)}
              className={
                active
                  ? "min-h-20 rounded-xl border border-emerald-300/30 bg-emerald-300/10 px-4 py-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300"
                  : "min-h-20 rounded-xl border border-white/10 bg-black/10 px-4 py-3 text-left transition hover:border-white/20 hover:bg-white/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
              }
            >
              <span className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium text-slate-100">
                  {tab.label}
                </span>
                <span className="rounded-full bg-white/[0.06] px-2 py-0.5 text-[11px] text-slate-400">
                  {tab.count}
                </span>
              </span>
              <span className="mt-1 block text-xs leading-5 text-slate-500">
                {tab.description}
              </span>
            </button>
          );
        })}
      </div>

      <section
        id={`asset-panel-${view}`}
        role="tabpanel"
        aria-labelledby={`asset-tab-${view}`}
        className="rounded-2xl border border-white/[0.08] bg-black/[0.08] p-4 md:p-5"
      >
        {isPending ? (
          <LoadingState />
        ) : view === "strategies" ? (
          <StrategyAssets
            drafts={drafts.data ?? []}
            versions={strategyVersions}
            outcomes={strategyOutcomes}
            error={firstError(drafts.error, versions.error, outcomes.error)}
          />
        ) : view === "components" ? (
          <ComponentAssets
            candidates={componentCandidates}
            evidence={evidence.data ?? []}
            factors={factorItems}
            promotePending={promoteFactor.isPending}
            onPromote={(candidate) => promoteFactor.mutate(candidate)}
            error={firstError(components.error, evidence.error)}
          />
        ) : (
          <FactorAssets
            factors={factorItems}
            componentCount={componentCandidates.length}
            statusPending={archiveFactor.isPending}
            onStatusChange={(factorId, restore) =>
              archiveFactor.mutate({ factorId, restore })
            }
            error={firstError(factors.error)}
          />
        )}
      </section>
    </div>
  );
}

function StrategyAssets({
  drafts,
  versions,
  outcomes,
  error,
}: {
  drafts: StrategyDraft[];
  versions: StrategyVersion[];
  outcomes: StrategyOutcome[];
  error: string | null;
}) {
  const versionsByStrategy = useMemo(() => {
    const grouped = new Map<string, StrategyVersion[]>();
    for (const version of versions) {
      grouped.set(version.strategy_id, [
        ...(grouped.get(version.strategy_id) ?? []),
        version,
      ]);
    }
    return grouped;
  }, [versions]);
  const outcomeByVersion = useMemo(
    () =>
      new Map(
        outcomes.map((outcome) => [outcome.strategy_version_id, outcome]),
      ),
    [outcomes],
  );

  if (error) return <ErrorState message={error} />;
  if (!drafts.length) {
    return (
      <EmptyState>
        尚无策略资产。去“研究工作台”输入第一条策略描述后，系统会保留原始来源；
        冻结基准和每次改进都会创建不可覆盖的新版本。
      </EmptyState>
    );
  }

  return (
    <div className="space-y-4">
      <SectionHeading
        title="策略生命周期"
        description="每张卡片代表一条原始策略；版本状态和最终研究结论分别保留。"
      />
      <div className="grid gap-4 xl:grid-cols-2">
        {drafts.map((draft) => {
          const draftVersions = [...(versionsByStrategy.get(draft.id) ?? [])].sort(
            (left, right) => right.version - left.version,
          );
          const strongest = strongestStrategyState(draftVersions, outcomeByVersion);
          return (
            <article
              key={draft.id}
              className="rounded-xl border border-white/10 bg-white/[0.025] p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-medium text-slate-100">
                    {draft.source_name?.trim() || sourceLabel(draft.source_type)}
                  </h3>
                  <div className="mt-1 text-xs text-slate-500">
                    {sourceLabel(draft.source_type)} ·{" "}
                    {formatDate(draft.created_at)}
                  </div>
                </div>
                <StatusBadge
                  value={strongest.label}
                  tone={strongest.tone}
                />
              </div>
              <p className="mt-3 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-slate-400">
                {draft.raw_content}
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                {draftVersions.length ? (
                  draftVersions.map((version) => {
                    const outcome = outcomeByVersion.get(version.id);
                    return (
                      <span
                        key={version.id}
                        className="rounded-full border border-white/10 bg-black/10 px-2.5 py-1 text-xs text-slate-300"
                        title={version.id}
                      >
                        v{version.version} ·{" "}
                        {outcome
                          ? cnOutcome(outcome.outcome_type)
                          : cnStatus(version.status)}
                      </span>
                    );
                  })
                ) : (
                  <span className="text-xs text-slate-500">
                    尚未冻结基准版本
                  </span>
                )}
              </div>
              <TechnicalDetails label="查看策略对象">
                <TechnicalId label="策略草稿" value={draft.id} />
                {draft.baseline_version_id ? (
                  <TechnicalId
                    label="冻结基准"
                    value={draft.baseline_version_id}
                  />
                ) : null}
              </TechnicalDetails>
            </article>
          );
        })}
      </div>
    </div>
  );
}

function ComponentAssets({
  candidates,
  evidence,
  factors,
  promotePending,
  onPromote,
  error,
}: {
  candidates: ComponentCandidate[];
  evidence: ComponentEvidence[];
  factors: FactorRegistryItem[];
  promotePending: boolean;
  onPromote: (candidate: ComponentCandidate) => void;
  error: string | null;
}) {
  const evidenceById = useMemo(
    () => new Map(evidence.map((item) => [item.id, item])),
    [evidence],
  );
  const ordered = [...candidates].sort((left, right) => {
    if (Boolean(left.archived_at) !== Boolean(right.archived_at)) {
      return left.archived_at ? 1 : -1;
    }
    if (left.status !== right.status) {
      return left.status === "component_candidate" ? -1 : 1;
    }
    return Date.parse(right.created_at) - Date.parse(left.created_at);
  });
  const promotedComponentIds = new Set(
    factors
      .map((factor) => factor.metadata.source_component_candidate_id)
      .filter((value): value is string => typeof value === "string"),
  );

  if (error) return <ErrorState message={error} />;
  if (!ordered.length) {
    return (
      <EmptyState>
        当前没有沉淀组件。失败策略中的局部规则只有在保留来源、市场、周期和增量证据后，
        才会进入这里；不会因为完整策略失败就自动生成“有效因子”。
      </EmptyState>
    );
  }

  return (
    <div className="space-y-4">
      <SectionHeading
        title="策略研究中沉淀的组件"
        description="组件是策略的一部分；组件候选只代表值得继续验证，不代表已经跨市场有效。"
      />
      <div className="grid gap-3 lg:grid-cols-2">
        {ordered.map((candidate) => {
          const item = evidenceById.get(candidate.evidence_id);
          const incremental = bestIncrementalReturn(item);
          return (
            <article
              key={candidate.id}
              className="rounded-xl border border-white/10 bg-white/[0.025] p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-medium leading-6 text-slate-100">
                    {humanComponentName(candidate.name)}
                  </h3>
                  <div className="mt-1 text-xs text-slate-500">
                    {item ? cnComponentType(item.component_type) : "策略组件"}
                    {" · "}
                    {candidate.timeframe || item?.timeframe || "周期待补充"}
                  </div>
                </div>
                <StatusBadge
                  value={
                    candidate.archived_at
                      ? "已归档"
                      : cnOutcome(candidate.status)
                  }
                  tone={
                    candidate.status === "component_candidate" &&
                    !candidate.archived_at
                      ? "warning"
                      : "neutral"
                  }
                />
              </div>
              <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
                <AssetDetail
                  label="适用市场"
                  value={
                    humanMarketProfile(
                      candidate.target_market_profile ||
                        item?.target_market_profile ||
                        "",
                    )
                  }
                />
                <AssetDetail
                  label="样本外证据"
                  value={item ? cnEvidence(item.out_of_sample_status) : "待补充"}
                />
                <AssetDetail
                  label="证据级别"
                  value={item ? cnEvidence(item.evidence_level) : "待补充"}
                />
                <AssetDetail
                  label="相对基准改善"
                  value={
                    incremental === null
                      ? "未记录"
                      : formatSignedPercent(incremental)
                  }
                />
              </dl>
              <p className="mt-3 text-xs leading-5 text-slate-500">
                {candidate.status === "component_candidate"
                  ? "已达到组件候选层级，仍需独立样本外、成本和稳定性证据后才能讨论验证。"
                  : "仅保留为诊断经验，不能直接组合成策略或写入已验证因子库。"}
              </p>
              {candidate.status === "component_candidate" &&
              !candidate.archived_at ? (
                promotedComponentIds.has(candidate.id) ? (
                  <div className="mt-3 rounded-lg border border-emerald-300/15 bg-emerald-300/[0.06] p-3 text-xs leading-5 text-emerald-100">
                    已登记为独立因子候选；仍需按市场、周期和成本单独验证。
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => onPromote(candidate)}
                    disabled={promotePending}
                    className="mt-3 min-h-11 w-full rounded-xl border border-sky-300/25 bg-sky-300/10 px-3 text-sm font-medium text-sky-100 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    沉淀为独立因子候选
                  </button>
                )
              ) : null}
              <TechnicalDetails label="查看组件证据">
                <TechnicalId label="组件对象" value={candidate.id} />
                <TechnicalId label="证据对象" value={candidate.evidence_id} />
                <TechnicalId label="原始名称" value={candidate.name} />
                {item ? (
                  <TechnicalId
                    label="来源策略版本"
                    value={item.source_strategy_version_id}
                  />
                ) : null}
              </TechnicalDetails>
            </article>
          );
        })}
      </div>
    </div>
  );
}

function FactorAssets({
  factors,
  componentCount,
  statusPending,
  onStatusChange,
  error,
}: {
  factors: FactorRegistryItem[];
  componentCount: number;
  statusPending: boolean;
  onStatusChange: (factorId: string, restore: boolean) => void;
  error: string | null;
}) {
  if (error) return <ErrorState message={error} />;
  if (!factors.length) {
    return (
      <div className="space-y-4">
        <EmptyState>
          当前独立因子库为 0。系统已经保留 {componentCount} 个策略组件记录，但组件不会自动
          变成因子。只有定义清晰、可跨策略复用，并在指定市场、周期、手续费和滑点条件下
          重新验证后，才登记为独立因子。
        </EmptyState>
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 text-xs leading-6 text-slate-500">
          因子库为空不是数据丢失：目前研究结论主要沉淀在“可复用组件”中，其中只有
          component candidate 值得继续验证，当前还没有 validated factor。
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <SectionHeading
        title="独立因子注册表"
        description="因子思想可以复用，但验证状态必须按市场、周期和成本模型隔离。"
      />
      <div className="divide-y divide-white/[0.06]">
        {[...factors]
          .sort((left, right) => {
            if ((left.status === "retired") !== (right.status === "retired")) {
              return left.status === "retired" ? 1 : -1;
            }
            return Date.parse(right.updated_at) - Date.parse(left.updated_at);
          })
          .map((factor) => (
          <article
            key={factor.factor_id}
            className="grid gap-3 py-4 md:grid-cols-[minmax(0,1fr)_auto]"
          >
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-medium text-slate-100">
                  {factor.name}
                </h3>
                <StatusBadge
                  value={cnStatus(factor.status)}
                  tone={
                    factor.status === "validated" ||
                    factor.status === "production"
                      ? "positive"
                      : factor.status === "candidate"
                        ? "warning"
                        : "neutral"
                  }
                />
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {cnComponentType(factor.category)} · v{factor.version} · 更新于{" "}
                {formatDate(factor.updated_at)}
              </div>
              {factor.description ? (
                <p className="mt-2 text-sm leading-6 text-slate-400">
                  {factor.description}
                </p>
              ) : null}
              <TechnicalDetails label="查看因子定义">
                <TechnicalId label="因子对象" value={factor.factor_id} />
                {factor.formula_path ? (
                  <TechnicalId label="公式路径" value={factor.formula_path} />
                ) : null}
              </TechnicalDetails>
            </div>
            <button
              type="button"
              onClick={() =>
                onStatusChange(
                  factor.factor_id,
                  factor.status === "retired",
                )
              }
              disabled={statusPending}
              className="min-h-11 self-start rounded-xl border border-white/10 px-3 text-xs text-slate-300 transition hover:border-white/20 hover:bg-white/[0.04] disabled:cursor-not-allowed disabled:opacity-40"
            >
              {factor.status === "retired" ? "恢复候选" : "移出活跃列表"}
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}

function AssetMetric({
  label,
  value,
  helper,
  tone,
}: {
  label: string;
  value: number;
  helper: string;
  tone: "positive" | "warning" | "neutral";
}) {
  const valueColor = {
    positive: "text-emerald-200",
    warning: "text-amber-200",
    neutral: "text-slate-100",
  }[tone];
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-2 text-2xl font-semibold tabular-nums ${valueColor}`}>
        {value}
      </div>
      <div className="mt-1 text-[11px] leading-5 text-slate-500">{helper}</div>
    </div>
  );
}

function SectionHeading({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div>
      <h2 className="text-base font-semibold text-slate-100">{title}</h2>
      <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
    </div>
  );
}

function AssetDetail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-black/10 p-3">
      <dt className="text-slate-500">{label}</dt>
      <dd className="mt-1 break-words leading-5 text-slate-200">{value}</dd>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="grid gap-3 md:grid-cols-2" aria-label="正在读取研究资产">
      {[0, 1, 2, 3].map((item) => (
        <div
          key={item}
          className="h-36 animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.025]"
        />
      ))}
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-rose-300/20 bg-rose-300/[0.06] p-4 text-sm leading-6 text-rose-100">
      无法读取研究资产：{message}
    </div>
  );
}

function firstError(...errors: Array<Error | null>): string | null {
  return errors.find(Boolean)?.message ?? null;
}

function strongestStrategyState(
  versions: StrategyVersion[],
  outcomes: Map<string, StrategyOutcome>,
): {
  label: string;
  tone: "positive" | "warning" | "danger" | "neutral";
} {
  const versionOutcomes = versions.flatMap((version) => {
    const outcome = outcomes.get(version.id);
    return outcome ? [outcome] : [];
  });
  if (versionOutcomes.some((item) => item.outcome_type === "validated")) {
    return { label: "已验证策略", tone: "positive" };
  }
  if (
    versionOutcomes.some((item) => item.outcome_type === "strategy_candidate")
  ) {
    return { label: "待验证策略", tone: "warning" };
  }
  if (
    versionOutcomes.some((item) => item.outcome_type === "rejected") ||
    versions.some((item) => item.status === "rejected")
  ) {
    return { label: "完整策略未通过", tone: "danger" };
  }
  if (versions.some((item) => item.status === "candidate")) {
    return { label: "存在候选版本", tone: "warning" };
  }
  if (versions.some((item) => item.status === "baseline")) {
    return { label: "冻结基准", tone: "neutral" };
  }
  return { label: "原始策略", tone: "neutral" };
}

function bestIncrementalReturn(item: ComponentEvidence | undefined) {
  if (!item) return null;
  for (const key of [
    "best_incremental_net_return",
    "incremental_net_return",
    "validation_net_return_delta_vs_baseline",
  ]) {
    const value = item.incremental_metrics[key];
    if (Number.isFinite(value)) return value;
  }
  return null;
}

function sourceLabel(sourceType: string) {
  return {
    natural_language: "自然语言策略",
    pine: "Pine Script 策略",
    file: "文件导入策略",
  }[sourceType] ?? "策略来源";
}

function humanComponentName(name: string) {
  return (
    {
      "BOLL-RSI minimum middle-band reward admission":
        "BOLL-RSI 中轨最小收益空间过滤",
      "BOLL-RSI max stop-distance admission": "BOLL-RSI 最大止损距离过滤",
      "BOLL-RSI early_time_stop timing": "BOLL-RSI 提前时间止损",
      "EMA structure-exit confirmation speed": "EMA 结构离场确认速度",
      "confirmation-candle breakout entry": "确认 K 线突破入场",
    }[name] ?? name
  );
}

function humanMarketProfile(value: string) {
  return (
    ({
      "crypto_perpetual.binance.eth": "币安 ETH/USDT 永续",
      "crypto_perpetual.okx.eth": "OKX ETH/USDT 永续",
    }[value] ?? value) || "待补充"
  );
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
