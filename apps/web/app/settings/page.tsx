import { PageFrame } from "@/components/page-frame";
import { CommercialLicensePanel } from "@/components/commercial-license-panel";
import { LocalAiMcpPanel } from "@/components/local-ai-mcp-panel";
import { SettingsStatus } from "@/components/settings-status";

export default function SettingsPage() {
  return (
    <PageFrame
      eyebrow="本地设置"
      title="运行方式、资源与安全状态"
      description="查看本机授权、运行方式和安全边界；许可证在本地离线验证，不要求登录或连接授权服务器。"
    >
      <div className="space-y-6">
        <CommercialLicensePanel />
        <LocalAiMcpPanel />
        <SettingsStatus />
      </div>
    </PageFrame>
  );
}
