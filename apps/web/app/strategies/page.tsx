import { PageFrame } from "@/components/page-frame";
import { ResearchAssetLibrary } from "@/components/research-asset-library";

export default function StrategiesPage() {
  return (
    <PageFrame
      eyebrow="研究资产"
      title="策略与因子资产库"
      description="统一查看策略版本、研究中自然沉淀的可复用组件和独立因子。候选、诊断证据与已验证结论严格分开，不把“少亏”或单次历史改善误写成有效策略。"
    >
      <ResearchAssetLibrary />
    </PageFrame>
  );
}
