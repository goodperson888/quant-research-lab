import { JobList } from "@/components/job-list";
import { PageFrame } from "@/components/page-frame";

export default function JobsPage() {
  return (
    <PageFrame
      eyebrow="Local Worker"
      title="Jobs 与执行日志"
      description="API只创建白名单Job；长回测由独立本地Worker处理。阶段0没有默认处理器，不会在HTTP请求内运行回测。"
    >
      <JobList />
    </PageFrame>
  );
}
