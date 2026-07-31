import { PageFrame } from "@/components/page-frame";
import { SettingsStatus } from "@/components/settings-status";

export default function SettingsPage() {
  return (
    <PageFrame
      eyebrow="本地设置"
      title="运行方式、资源与安全状态"
      description="先查看日常使用最相关的运行方式和安全边界；模型能力、版本策略等技术配置收在高级信息中。"
    >
      <SettingsStatus />
    </PageFrame>
  );
}
