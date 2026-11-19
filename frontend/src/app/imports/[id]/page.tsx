import { Dashboard } from "@/components/dashboard/dashboard";

export default async function ImportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <Dashboard importId={id} />;
}
