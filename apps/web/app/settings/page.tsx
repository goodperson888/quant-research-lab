import { PageFrame } from "@/components/page-frame";
import { SettingsStatus } from "@/components/settings-status";

export default function SettingsPage() {
  return (
    <PageFrame
      eyebrow="Local Configuration"
      title="Provider、模型能力与安全状态"
      description="阶段0固定为 external_local_agent + local_runtime，并执行现代模型能力门禁。其余 Provider 和 Hosted Sandbox 只冻结协议，不伪装可用。"
    >
      <SettingsStatus />
    </PageFrame>
  );
}
