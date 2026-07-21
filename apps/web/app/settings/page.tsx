import { PageFrame } from "@/components/page-frame";
import { SettingsStatus } from "@/components/settings-status";

export default function SettingsPage() {
  return (
    <PageFrame
      eyebrow="Local Configuration"
      title="Provider、执行目标与安全状态"
      description="阶段0固定为 external_local_agent + local_runtime。其余Provider和Hosted Sandbox只冻结协议，不伪装可用。"
    >
      <SettingsStatus />
    </PageFrame>
  );
}
