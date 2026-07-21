import { EmptyState, PageFrame } from "@/components/page-frame";

export default function ReportsPage() {
  return (
    <PageFrame
      eyebrow="Research Evidence"
      title="基准与实验报告"
      description="未来展示基准、消融、样本外、压力测试、Trial对比和稳定平台；报告必须链接不可变Run与数据版本。"
    >
      <EmptyState
        title="尚无产品化报告"
        body="第一份真实策略尚未提供。阶段0不把已有工程烟雾验证包装成策略收益。"
      />
    </PageFrame>
  );
}
