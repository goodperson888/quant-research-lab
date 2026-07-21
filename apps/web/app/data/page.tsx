import { DataSummaryPanel } from "@/components/data-summary";
import { PageFrame } from "@/components/page-frame";

export default function DataPage() {
  return (
    <PageFrame
      eyebrow="Market Profile"
      title="数据范围与质量缺口"
      description="只读展示当前ETH/USDT永续数据摘要。Parquet仍是研究权威层，Web不直接读取DuckDB或SQLite。"
    >
      <DataSummaryPanel />
    </PageFrame>
  );
}
