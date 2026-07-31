import { DataSummaryPanel } from "@/components/data-summary";
import { PageFrame } from "@/components/page-frame";

export default function DataPage() {
  return (
    <PageFrame
      eyebrow="数据中心"
      title="市场数据"
      description="查看当前回测数据的来源、时间范围、周期、行数和质量缺口。网页只读展示已登记的数据版本，不会修改研究原始文件。"
    >
      <DataSummaryPanel />
    </PageFrame>
  );
}
