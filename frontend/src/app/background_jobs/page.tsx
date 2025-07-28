"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import { useJobs } from "../lib/useJobs";
import { deleteJobs } from "../lib/jobsAPI";
import { useState } from "react";
import { Trash2, ScrollText, Wand2, Loader2, Sparkles } from "lucide-react";

export default function BackgroundJobsPage() {
  const { user, token } = useAuth();
  const { jobs, mutate } = useJobs();
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sortDesc, setSortDesc] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const allowed = useRoleRedirect("system admin");
  if (!allowed) return null;

  const activeStatuses = ["queued", "running", "processing"];

  // Filtering/sorting
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
    setMessage("Arcane Tasks banished!");
    setTimeout(() => setMessage(""), 2000);
    setSelected([]);
    mutate();
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-gradient-to-br from-slate-100 to-indigo-50 dark:from-[#23153a] dark:to-[#371a41] px-2 sm:px-4 py-8 transition-colors">
          <div className="mx-auto max-w-5xl flex flex-col gap-6 w-full">
            {/* HEADER */}
            <div className="flex flex-col items-center gap-1 mb-4">
              <div className="flex items-center justify-center gap-2 mb-1">
                <Sparkles className="w-8 h-8 text-purple-500 animate-pulse" />
                <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight font-serif text-indigo-900 dark:text-indigo-200 drop-shadow">
                  Arcane Task Ledger
                </h1>
              </div>
              <p className="text-md md:text-lg text-slate-600 dark:text-slate-200 italic text-center">
                Gaze into the background magics. Here, all tasks—mundane and mystical—are tracked by the Loremaster.
              </p>
              {message && (
                <div className="bg-purple-700 text-white px-6 py-2 rounded-xl shadow text-sm animate-fade-in-out mt-2">
                  {message}
                </div>
              )}
            </div>

            {/* FILTERS */}
            <div className="flex flex-wrap gap-4 items-center bg-white/80 dark:bg-indigo-900/40 rounded-2xl shadow p-4 border-2 border-indigo-200 dark:border-indigo-700">
              <select
                className="px-4 py-2 rounded-xl border-2 border-indigo-300 dark:border-indigo-700 bg-white/90 dark:bg-indigo-950 text-base font-semibold focus:ring-2 focus:ring-indigo-400"
                value={typeFilter}
                onChange={e => setTypeFilter(e.target.value)}
              >
                <option value="">All Rites</option>
                {types.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
              <select
                className="px-4 py-2 rounded-xl border-2 border-indigo-300 dark:border-indigo-700 bg-white/90 dark:bg-indigo-950 text-base font-semibold focus:ring-2 focus:ring-indigo-400"
                value={statusFilter}
                onChange={e => setStatusFilter(e.target.value)}
              >
                <option value="">All Status</option>
                {statuses.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <button
                className="ml-auto flex items-center gap-2 px-5 py-2 rounded-xl font-bold bg-rose-600 text-white shadow hover:bg-rose-800 transition disabled:opacity-40"
                onClick={handleDelete}
                disabled={selected.length === 0}
                title="Banishing is disabled for running rituals"
              >
                <Trash2 className="w-5 h-5" /> Banish Selected
              </button>
            </div>

            {/* JOBS TABLE */}
            <div className="overflow-x-auto rounded-2xl shadow border border-indigo-100 dark:border-indigo-800 bg-white/70 dark:bg-indigo-950/50 mt-2">
              <table className="min-w-full text-sm rounded-xl overflow-hidden">
                <thead>
                  <tr className="bg-gradient-to-r from-indigo-100 to-indigo-200 dark:from-indigo-900 dark:to-indigo-800">
                    <th className="px-2 py-2 text-center"></th>
                    <th className="px-2 py-2">Rite</th>
                    <th className="px-2 py-2">Scroll ID</th>
                    <th
                      className="px-2 py-2 cursor-pointer select-none hover:underline"
                      onClick={() => setSortDesc(!sortDesc)}
                    >
                      Began {sortDesc ? '▼' : '▲'}
                    </th>
                    <th className="px-2 py-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(j => (
                    <JobRow
                      key={j.kind + j.job_id}
                      job={j}
                      selected={selected.includes(j.kind + j.job_id)}
                      toggle={toggle}
                      disable={activeStatuses.includes(j.status)}
                    />
                  ))}
                  {sorted.length === 0 && (
                    <tr>
                      <td colSpan={5} className="text-center p-10 text-lg text-slate-400 italic">
                        No Arcane Tasks found.<br />The ley lines are calm...
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}

// -- RPG Themed JobRow
function JobRow({ job, selected, toggle, disable }: any) {
  const [open, setOpen] = useState(false);

  // Optionally: status color/rune badges
  const statusColor = {
    success: "bg-green-200 dark:bg-green-800 text-green-900 dark:text-green-100",
    failed: "bg-rose-200 dark:bg-rose-800 text-rose-900 dark:text-rose-100",
    running: "bg-yellow-100 dark:bg-yellow-700 text-yellow-900 dark:text-yellow-100 animate-pulse",
    queued: "bg-indigo-100 dark:bg-indigo-800 text-indigo-900 dark:text-indigo-100",
    processing: "bg-blue-100 dark:bg-blue-900 text-blue-900 dark:text-blue-100 animate-pulse",
  }[job.status] || "bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-slate-100";

  const icon = {
    queued: <Wand2 className="w-5 h-5 inline-block mr-1 text-indigo-500" />,
    running: <Loader2 className="w-5 h-5 inline-block mr-1 animate-spin text-yellow-500" />,
    processing: <Sparkles className="w-5 h-5 inline-block mr-1 text-blue-400" />,
    success: <ScrollText className="w-5 h-5 inline-block mr-1 text-green-600" />,
    failed: <Trash2 className="w-5 h-5 inline-block mr-1 text-rose-500" />,
  }[job.status] || <Wand2 className="w-5 h-5 inline-block mr-1 text-slate-400" />;

  return (
    <>
      <tr className="hover:bg-indigo-100/30 dark:hover:bg-indigo-800/30 transition">
        <td className="text-center px-2 py-2">
          <input
            type="checkbox"
            disabled={disable}
            checked={selected}
            onChange={() => toggle(job.kind + job.job_id)}
            aria-label="Select Arcane Task"
            className="scale-125 accent-indigo-500"
          />
        </td>
        <td className="px-2 py-2 font-bold font-serif flex items-center gap-2">
          {icon}
          <span>{job.kind}</span>
        </td>
        <td
          className="px-2 py-2 font-mono underline cursor-pointer text-indigo-700 dark:text-indigo-200"
          title="Show details"
          onClick={() => setOpen(o => !o)}
        >
          {job.job_id}
        </td>
        <td className="px-2 py-2">{job.start_time ? new Date(job.start_time).toLocaleString() : "-"}</td>
        <td className="px-2 py-2">
          <span
            className={
              `rounded-full px-3 py-1 text-xs font-semibold shadow-md border ${statusColor}`
            }
          >
            {job.status}
          </span>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5} className="bg-indigo-50 dark:bg-indigo-900/70 px-4 py-3 text-xs whitespace-pre-wrap font-mono border-t border-indigo-200 dark:border-indigo-800">
            <span className="text-indigo-800 dark:text-indigo-200 font-bold">Arcane Details:</span>
            <br />
            {JSON.stringify(job, null, 2)}
          </td>
        </tr>
      )}
    </>
  );
}
