import { PageFrame } from "@/components/page-frame";
import { StrategyList } from "@/components/strategy-list";

export default function StrategiesPage() {
  return (
    <PageFrame
      eyebrow="Strategy Registry"
      title="策略与不可变版本"
      description="保存来源、draft、baseline v0 和后续 Proposal。任何改进都创建新版本，不覆盖冻结基准。"
    >
      <StrategyList />
    </PageFrame>
  );
}
