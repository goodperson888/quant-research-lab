import { JobList } from "@/components/job-list";
import { PageFrame } from "@/components/page-frame";

export default function JobsPage() {
  return (
    <PageFrame
      eyebrow="运行中心"
      title="后台任务"
      description="查看回测、参数试验和诊断任务的排队、运行、完成与停止原因。技术 ID 默认收起，需要排错时再展开。"
    >
      <JobList />
    </PageFrame>
  );
}
