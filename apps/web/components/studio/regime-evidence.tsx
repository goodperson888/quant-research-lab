import { cnEvidence, cnRegime, formatNumber, formatPercent } from "@/components/studio/studio-labels";
import { EmptyState, StatusBadge } from "@/components/studio/studio-primitives";

export function RegimeEvidence({
  metrics,
  evidenceStatus,
  formalValidation,
}: {
  metrics: Record<string, Record<string, number>>;
  evidenceStatus: string;
  formalValidation: boolean;
}) {
  const rows = Object.entries(metrics).sort(
    (left, right) =>
      finiteOrZero(right[1].trade_count) - finiteOrZero(left[1].trade_count),
  );
  if (!rows.length) {
    return (
      <EmptyState>
        尚无行情状态样本。可行性门槛前只能做使用已收盘数据的行情适配初查。
      </EmptyState>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge
          value={formalValidation ? "正式行情验证" : "行情适配初查"}
          tone={formalValidation ? "positive" : "warning"}
        />
        <StatusBadge value={cnEvidence(evidenceStatus)} tone="warning" />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] text-left text-xs tabular-nums">
          <thead className="text-slate-500">
            <tr>
              <th className="py-2 pr-4 font-medium">行情状态</th>
              <th className="py-2 pr-4 font-medium">样本量</th>
              <th className="py-2 pr-4 font-medium">净收益</th>
              <th className="py-2 pr-4 font-medium">盈亏效率</th>
              <th className="py-2 pr-4 font-medium">置信度提示</th>
              <th className="py-2 font-medium">证据判断</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, item]) => {
              const count = finiteOrZero(item.trade_count);
              return (
                <tr key={name} className="border-t border-white/[0.06] text-slate-300">
                  <td className="py-3 pr-4">{cnRegime(name)}</td>
                  <td className="py-3 pr-4">{formatNumber(count, 0)} 笔</td>
                  <td className="py-3 pr-4">{formatPercent(item.net_return)}</td>
                  <td className="py-3 pr-4">{formatNumber(item.profit_factor, 2)}</td>
                  <td className="py-3 pr-4">
                    {confidenceHint(item.confidence, count)}
                  </td>
                  <td className="py-3">
                    {count < 30
                      ? "证据不足"
                      : formalValidation
                        ? "扩展证据"
                        : "仅作初查"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <RegimeReturnChart rows={rows} />
      <p className="text-xs leading-5 text-amber-200">
        标签只使用当时可观察的已收盘 1 小时 K 线并延后一根生效。样本少时继续显示“证据不足”，不伪装成适合行情。
        未记录显式统计置信度时，页面只按交易样本量给出保守提示，不替代正式验证。
      </p>
    </div>
  );
}

function RegimeReturnChart({
  rows,
}: {
  rows: Array<[string, Record<string, number>]>;
}) {
  const maxAbs = Math.max(
    ...rows.map(([, item]) => Math.abs(finiteOrZero(item.net_return))),
    0.001,
  );
  return (
    <div className="rounded-xl border border-white/10 bg-black/10 p-3">
      <div className="mb-1 text-sm font-medium text-slate-200">
        各行情状态净收益对比
      </div>
      <div className="mb-4 text-xs leading-5 text-slate-500">
        中线为 0；左侧表示负收益，右侧表示正收益。条形旁保留精确数值，避免只靠颜色判断。
      </div>
      <div
        className="space-y-3"
        role="img"
        aria-label="各行情状态净收益水平条形图"
      >
        {rows.map(([name, item]) => {
          const value = finiteOrZero(item.net_return);
          const width = `${Math.max((Math.abs(value) / maxAbs) * 100, 2)}%`;
          return (
            <div
              key={name}
              className="grid grid-cols-[96px_minmax(120px,1fr)_72px] items-center gap-3 text-xs"
            >
              <div className="truncate text-slate-300" title={cnRegime(name)}>
                {cnRegime(name)}
              </div>
              <div className="grid h-5 grid-cols-2 overflow-hidden rounded bg-white/[0.04]">
                <div className="flex items-stretch justify-end border-r border-white/20">
                  {value < 0 ? (
                    <div
                      className="h-full rounded-l bg-rose-300/70"
                      style={{ width }}
                    />
                  ) : null}
                </div>
                <div>
                  {value >= 0 ? (
                    <div
                      className="h-full rounded-r bg-emerald-300/70"
                      style={{ width }}
                    />
                  ) : null}
                </div>
              </div>
              <div
                className={
                  value >= 0
                    ? "text-right text-emerald-200"
                    : "text-right text-rose-200"
                }
              >
                {formatPercent(value)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function confidenceHint(confidence: number | undefined, sampleCount: number) {
  if (Number.isFinite(confidence)) {
    const normalized = Math.max(0, Math.min(confidence ?? 0, 1));
    return `${(normalized * 100).toFixed(0)}%（记录值）`;
  }
  if (sampleCount < 30) return "低（样本不足）";
  if (sampleCount < 100) return "初步（样本有限）";
  return "样本较充分（仍需跨期）";
}

function finiteOrZero(value: number | undefined) {
  return Number.isFinite(value) ? (value ?? 0) : 0;
}
