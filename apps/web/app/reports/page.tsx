import { PageFrame } from "@/components/page-frame";
import { ReportList } from "@/components/report-list";

export default function ReportsPage() {
  return (
    <PageFrame
      eyebrow="研究证据"
      title="研究报告"
      description="按时间查看真实回测、参数试验、诊断和验证证据。报告保留运行、数据版本与研究对象链接，但不会自动等同于有效策略。"
    >
      <ReportList />
    </PageFrame>
  );
}
