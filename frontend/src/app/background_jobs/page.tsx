"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import { useJobs } from "../lib/useJobs";
import { deleteJobs } from "../lib/jobsAPI";
import { useState } from "react";
import { Trash2 } from "lucide-react";

export default function BackgroundJobsPage() {
  const { user, token } = useAuth();
  const { jobs, mutate } = useJobs();
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sortDesc, setSortDesc] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState(" ");

  const allowed = useRoleRedirect("system admin");
  if (!allowed) return null;

  const activeStatuses = ["queued", "running", "processing"];

  const filtered = jobs.filter((j: any) =>
    (!typeFilter || j.kind === typeFilter) &&
    (!statusFilter || j.status === statusFilter)
  );

  const sorted = [...filtered].sort((a, b) => {
    const av = a.start_time || "";
    const bv = b.start_time || "";
    if (av < bv) return sortDesc ? 1 : -1;
    if (av > bv) return sortDesc ? -1 : 1;
    return 0;
  });

  const types = Array.from(new Set(jobs.map((j: any) => j.kind)));
  const statuses = Array.from(new Set(jobs.map((j: any) => j.status)));

  function toggle(id: string) {
    setSelected(sel => sel.includes(id) ? sel.filter(x => x !== id) : [...sel, id]);
  }

  async function handleDelete() {
    const toDelete = sorted
      .filter(j => selected.includes(j.kind + j.job_id) && !activeStatuses.includes(j.status))
      .map(j => ({ kind: j.kind, job_id: j.job_id }));
    if (toDelete.length === 0) return;
    await deleteJobs(toDelete, token || "");
    setMessage("Jobs removed");
    setTimeout(() => setMessage(""), 2000);
    setSelected([]);
    mutate();
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] transition-colors duration-300 px-2 sm:px-6 py-8">
          <div className="mx-auto max-w-5xl w-full flex flex-col gap-6">
            <div className="flex items-center justify-between mb-3">
              <h1 className="text-2xl font-serif font-bold text-[var(--primary)]">Background Jobs</h1>
              {message && (
                <div className="bg-[var(--primary)] text-[var(--primary-foreground)] px-4 py-2 rounded-xl shadow text-sm animate-fade-in-out">
                  {message}
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-4 items-center bg-[var(--surface-variant)]/60 rounded-xl p-3">
              <select
                className="px-3 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] focus:outline-none text-base"
                value={typeFilter}
                onChange={e => setTypeFilter(e.target.value)}
              >
                <option value="">All types</option>
                {types.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
              <select
                className="px-3 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] focus:outline-none text-base"
                value={statusFilter}
                onChange={e => setStatusFilter(e.target.value)}
              >
                <option value="">All status</option>
                {statuses.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <button
                className="ml-auto flex items-center gap-2 px-4 py-2 rounded-xl font-bold bg-rose-600 text-white shadow hover:bg-rose-800 transition"
                onClick={handleDelete}
              >
                <Trash2 className="w-4 h-4" /> Remove Selected
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full border border-[var(--border)] text-sm">
                <thead className="bg-[var(--surface)]">
                  <tr>
                    <th className="border px-2 py-1"></th>
                    <th className="border px-2 py-1">Type</th>
                    <th className="border px-2 py-1">Job ID</th>
                    <th className="border px-2 py-1 cursor-pointer" onClick={() => setSortDesc(!sortDesc)}>
                      Start {sortDesc ? '▼' : '▲'}
                    </th>
                    <th className="border px-2 py-1">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(j => (
                    <JobRow key={j.kind + j.job_id} job={j} selected={selected.includes(j.kind + j.job_id)} toggle={toggle} disable={activeStatuses.includes(j.status)} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}

function JobRow({ job, selected, toggle, disable }: any) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="hover:bg-[var(--surface)]/40">
        <td className="border px-2 py-1 text-center">
          <input type="checkbox" disabled={disable} checked={selected} onChange={() => toggle(job.kind + job.job_id)} />
        </td>
        <td className="border px-2 py-1">{job.kind}</td>
        <td className="border px-2 py-1 font-mono underline cursor-pointer" onClick={() => setOpen(o => !o)}>
          {job.job_id}
        </td>
        <td className="border px-2 py-1">{job.start_time ? new Date(job.start_time).toLocaleString() : '-'}</td>
        <td className="border px-2 py-1">{job.status}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5} className="border px-2 py-2 bg-[var(--surface)] text-xs whitespace-pre-wrap">
            {JSON.stringify(job, null, 2)}
          </td>
        </tr>
      )}
    </>
  );
}
