import { ReportDetail } from "@/components/report-detail";

export default async function ReportDetailPage({
  params,
}: {
  params: Promise<{ bundleId: string }>;
}) {
  const { bundleId } = await params;
  return <ReportDetail bundleId={bundleId} />;
}
