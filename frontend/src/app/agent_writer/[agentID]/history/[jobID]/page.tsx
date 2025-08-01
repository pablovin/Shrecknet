"use client";
import { useParams, useRouter } from "next/navigation";
import DashboardLayout from "@/app/components/DashboardLayout";
import AuthGuard from "@/app/components/auth/AuthGuard";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { useEffect, useState } from "react";
import { getWriterJob, startAnalyzeJob } from "@/app/lib/agentAPI";
import { Loader2 } from "lucide-react";

export default function HistoryJobPage() {
  const { agentID, jobID } = useParams();
  const { token } = useAuth();
  const router = useRouter();
  const [job, setJob] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!token || !jobID) return;
    getWriterJob(jobID as string, token)
      .then(setJob)
      .catch(() => {});
  }, [token, jobID]);

  async function handleRedo() {
    if (!job || !token) return;
    setLoading(true);
    try {
      await startAnalyzeJob(Number(agentID), job.page_ids || [], token);
      router.push(`/agent_writer?agent=${agentID}`);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }

  function handleReviewAgain() {
    if (!job) return;
    const path = `/agent_writer/${agentID}/${job.job_type === 'analyze_pages' ? 'suggestions' : 'review'}/${job.job_id}`;
    router.push(path);
  }

  if (!job) return (
    <AuthGuard><DashboardLayout>Loading...</DashboardLayout></AuthGuard>
  );

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full p-4 space-y-4">
          <h1 className="text-xl font-bold">Request {jobID}</h1>
          <div className="flex gap-2">
            <button onClick={handleRedo} disabled={loading}
              className="px-3 py-2 rounded bg-fuchsia-600 text-white disabled:opacity-50">
              {loading ? <Loader2 className="w-4 h-4 animate-spin"/> : "Redo"}
            </button>
            <button onClick={handleReviewAgain}
              className="px-3 py-2 rounded bg-indigo-600 text-white">
              Review Again
            </button>
          </div>
          <div className="space-y-4">
            <div>
              <h2 className="font-semibold">Analysis</h2>
              <pre className="bg-gray-100 p-2 overflow-auto text-xs">
{JSON.stringify(job.analysis || job.suggestions || [], null, 2)}</pre>
            </div>
            <div>
              <h2 className="font-semibold">Review</h2>
              <pre className="bg-gray-100 p-2 overflow-auto text-xs">
{JSON.stringify(job.review || {}, null, 2)}</pre>
            </div>
            <div>
              <h2 className="font-semibold">Generated</h2>
              <pre className="bg-gray-100 p-2 overflow-auto text-xs">
{JSON.stringify(job.generated || job.pages || [], null, 2)}</pre>
            </div>
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
