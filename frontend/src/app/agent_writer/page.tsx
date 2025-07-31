"use client";
export const dynamic = "force-dynamic";
import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import { useAgents } from "../lib/useAgents";
import { useWorlds } from "../lib/userWorlds";
import { usePages } from "../lib/usePage";
import { useConcepts } from "../lib/useConcept";
import { startAnalyzeJob, getWriterJob, updateWriterJob } from "../lib/agentAPI";
import { useWriterJobs } from "../lib/useWriterJobs";
import Image from "next/image";
import Link from "next/link";
import { BookOpenText, Search, Sparkles, Feather, Undo2, ArrowLeftCircle } from "lucide-react";
import { useTranslation } from "../hooks/useTranslation";

const AGENT_PERSONALITIES = {
  "Lorekeeper Lyra": "“A tale untold is a world unseen. Let’s fill these pages with legend!”",
  "Archivist Axion": "“Every great saga begins with a single spark. Shall we kindle it together?”",
  "Chronicle": "“Let us chronicle your world for generations of adventurers!”",
  "default": "“I will help you turn knowledge into stories!”"
};

const JOB_LABELS = {
  analyze_pages: "Analyze Pages",
  generate_pages: "Update/Create Pages",
};

function AgentWriterPageContent() {
  const { user, token } = useAuth();
  const { t } = useTranslation();
  const { agents, isLoading: agentsLoading } = useAgents();
  const { worlds } = useWorlds();
  const [selectedAgent, setSelectedAgent] = useState<any>(null);
  const searchParams = useSearchParams();
  const [selectedConcept, setSelectedConcept] = useState<number | "">("");
  const { pages } = usePages(
    selectedAgent
      ? {
          gameworld_id: selectedAgent.world_id,
          ...(selectedConcept ? { concept_id: selectedConcept } : {}),
        }
      : {}
  );
  const { concepts } = useConcepts(selectedAgent?.world_id);

  const [search, setSearch] = useState("");
  const [sortField, setSortField] = useState("name");
  const [sortAsc, setSortAsc] = useState(true);
  const [pageIndex, setPageIndex] = useState(0);
  const PAGE_SIZE = 10;
  const [selectedPages, setSelectedPages] = useState<number[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);
  const { jobs: writerJobs, mutate: mutateWriterJobs } = useWriterJobs();
  const [jobFeedback, setJobFeedback] = useState<string | null>(null);

  useEffect(() => {
    if (selectedAgent || agents.length === 0) return;
    const param = searchParams.get("agent");
    if (param) {
      const ag = agents.find((a) => a.id === Number(param));
      if (ag) setSelectedAgent(ag);
    }
  }, [agents, searchParams, selectedAgent]);

  useEffect(() => {
    const interval = setInterval(() => {
      jobs.forEach((job) => {
        if (job.status === "done") return;
        getWriterJob(job.id, token)
          .then((data) => {
            setJobs((j) =>
              j.map((jj) => (jj.id === job.id ? { ...jj, ...data } : jj))
            );
          })
          .catch(() => {});
      });
    }, 4000);
    return () => clearInterval(interval);
  }, [jobs, token]);

  const allowed = useRoleRedirect("writer");
  if (!allowed) return null;

  const writerAgents = agents.filter(a => a.task === "page writer");
  const worldsMap = Object.fromEntries(worlds.map(w => [w.id, w]));

  // --- Scriptorium Welcome: Choose your Scribe ---
  if (!selectedAgent) {
    return (
      <AuthGuard>
        <DashboardLayout>
          <div className="min-h-screen w-full text-indigo-900 px-2 sm:px-6 py-12">
            <div className="mx-auto max-w-3xl flex flex-col gap-8 items-center">
              {/* Scriptorium Hero */}
              <div className="w-full flex flex-col items-center gap-4 bg-gradient-to-br from-indigo-100/70 via-fuchsia-100/80 to-white/80 rounded-2xl shadow-xl p-8 border border-indigo-200">
                <BookOpenText className="w-12 h-12 text-indigo-400 mb-2" />
                <h1 className="text-3xl font-bold text-indigo-700 text-center font-serif mb-1">{t('scribe_welcome')}</h1>
                <p className="text-center text-lg text-indigo-900/80 mb-2">{t('scribe_intro')}</p>
                <p className="text-center text-indigo-800/70 text-base">
                  {t('scribe_select')}<br/>
                  <span className="italic text-fuchsia-700">{t('scribe_pen_line')}</span>
                </p>
              </div>
              {/* Scribe Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-8 mt-6 w-full">
                {agentsLoading ? (
                  <div className="col-span-2 text-center text-lg">{t('summoning_scribes')}</div>
                ) : (
                  writerAgents.map(a => (
                    <button
                      key={a.id}
                      onClick={() => setSelectedAgent(a)}
                      className="flex flex-col items-center gap-2 p-6 rounded-2xl shadow-lg border border-indigo-200 bg-white hover:scale-105 hover:shadow-2xl transition-all"
                    >
                      <Image src={a.logo || "/images/default/avatars/logo.png"} alt={a.name} width={100} height={100} className="rounded-full object-cover border-2 border-fuchsia-300 shadow mb-2" />
                      <span className="text-xl font-bold text-indigo-800">{a.name}</span>
                      <span className="text-sm text-fuchsia-700">{worldsMap[a.world_id]?.name}</span>
                      <span className="italic text-xs text-indigo-400 text-center">
                        {AGENT_PERSONALITIES[a.name] || AGENT_PERSONALITIES.default}
                      </span>
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>
        </DashboardLayout>
      </AuthGuard>
    );
  }

  // ---- After agent selected ----
  const conceptMap = Object.fromEntries((concepts || []).map(c => [c.id, c]));
  const pageMap = Object.fromEntries((pages || []).map(p => [p.id, p]));

  const agentWriterJobs = writerJobs.filter(j => j.agent_id === selectedAgent.id);
  const runningJobs = agentWriterJobs.filter(j => j.status !== 'done');
  const waitingJobs = agentWriterJobs.filter(j => j.status === 'done' && j.action_needed === 'review');
  const doneJobs = agentWriterJobs
    .filter(j => j.status === 'done' && j.action_needed !== 'review')
    .sort((a, b) => new Date(b.end_time || 0).getTime() - new Date(a.end_time || 0).getTime())
    .slice(0, 3);
  const recentJobs = [...agentWriterJobs]
    .sort((a,b) => new Date(b.start_time || 0).getTime() - new Date(a.start_time || 0).getTime())
    .slice(0,5);

  let filtered = (pages || []).filter(
    p => p.name?.toLowerCase().includes(search.toLowerCase()) && p.content && p.content.trim() !== ""
  );

  filtered.sort((a, b) => {
    let va: any;
    let vb: any;
    if (sortField === "name") { va = a.name; vb = b.name; }
    else if (sortField === "concept") { va = conceptMap[a.concept_id]?.name || ""; vb = conceptMap[b.concept_id]?.name || ""; }
    else if (sortField === "autogenerated") { va = a.autogenerated_content ? 1 : 0; vb = b.autogenerated_content ? 1 : 0; }
    else { va = a.updated_at || ""; vb = b.updated_at || ""; }
    if (va < vb) return sortAsc ? -1 : 1;
    if (va > vb) return sortAsc ? 1 : -1;
    return 0;
  });

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE) || 1;
  const paginated = filtered.slice(pageIndex * PAGE_SIZE, pageIndex * PAGE_SIZE + PAGE_SIZE);

  function changeSort(field: string) {
    if (sortField === field) setSortAsc(!sortAsc); else { setSortField(field); setSortAsc(true); }
  }

  // ---- Main Render ----
  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full text-indigo-900 px-2 sm:px-6 py-10">
          <div className="mx-auto max-w-5xl w-full flex flex-col gap-8">

            {/* Scribe Header */}
            <div className="flex flex-col sm:flex-row items-center gap-6 bg-gradient-to-br from-indigo-100/80 via-fuchsia-100/80 to-white/80 p-6 rounded-2xl shadow-xl border border-fuchsia-200">
              <Image src={selectedAgent.logo || "/images/default/avatars/logo.png"} alt={selectedAgent.name} width={160} height={160} className="w-40 h-40 rounded-full object-cover border-2 border-fuchsia-300 shadow" />
              <div className="flex-1 flex flex-col gap-1">
                <h2 className="text-2xl font-extrabold text-fuchsia-800 mb-1">{selectedAgent.name}</h2>
                <span className="text-md text-indigo-700 mb-1">{worldsMap[selectedAgent.world_id]?.name || ""}</span>
                <div className="italic text-fuchsia-600 mb-1">{AGENT_PERSONALITIES[selectedAgent.name] || AGENT_PERSONALITIES.default}</div>
                <p className="mb-1">I am your Scribe for <span className="font-semibold">{worldsMap[selectedAgent.world_id]?.name}</span>.<br />
                  Show me which stories, chronicles, or pages to read, and together we’ll forge new legends for your world!
                </p>
              </div>
              <button
                onClick={() => { setSelectedAgent(null); setSearch(""); }}
                className="mt-4 sm:mt-0 sm:ml-auto flex gap-2 items-center px-4 py-2 rounded-xl bg-fuchsia-600 text-white font-semibold shadow hover:bg-fuchsia-800 transition"
              >
                <ArrowLeftCircle className="w-5 h-5" />
                {t('back_to_scribes')}
              </button>
            </div>

            {/* Jobs Table */}
            {(runningJobs.length > 0 || waitingJobs.length > 0 || doneJobs.length > 0) && (
              <div className="overflow-x-auto border border-indigo-200 rounded-xl bg-white/90 shadow">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-left text-indigo-800">
                      <th className="p-2">{t('status')}</th>
                      <th className="p-2">{t('page')}</th>
                      <th className="p-2">{t('source_pages')}</th>
                      <th className="p-2">{t('type')}</th>
                      <th className="p-2">{t('started')}</th>
                      <th className="p-2">{t('ended')}</th>
                      <th className="p-2">{t('duration')}</th>
                      <th className="p-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map(job => (
                      <tr key={job.id} className={`border-t border-indigo-100 ${job.status === 'done' ? 'bg-indigo-50/80' : 'bg-yellow-50/70 animate-pulse'}`}>
                        <td className={`p-2 ${job.status === 'done' ? 'text-fuchsia-700 font-semibold' : 'text-yellow-700 font-semibold'}`}>{job.status === 'done' ? t('needs_review') : t('running')}</td>
                        <td className="p-2">-</td>
                        <td className="p-2">{job.pages.join(', ')}</td>
                        <td className="p-2">{JOB_LABELS['analyze_pages']}</td>
                        <td className="p-2">{job.start_time ? new Date(job.start_time).toLocaleString() : '-'}</td>
                        <td className="p-2">{job.status === 'done' && job.end_time ? new Date(job.end_time).toLocaleString() : '-'}</td>
                        <td className="p-2">{job.start_time && job.end_time ? Math.round((new Date(job.end_time).getTime() - new Date(job.start_time).getTime())/1000) + 's' : '-'}</td>
                        <td className="p-2">{job.status === 'done' ? (<Link className="text-fuchsia-700 underline font-bold" href={`/agent_writer/${selectedAgent.id}/suggestions/${job.id}`}>{t('review')}</Link>) : null}</td>
                      </tr>
                    ))}
                    {runningJobs.map(job => {
                      const srcNames = Array.isArray(job.page_names)
                        ? job.page_names.join(', ')
                        : Array.isArray(job.suggestions)
                          ? Array.from(new Set(job.suggestions.flatMap((s: any) => (s.source_pages || []).map((sp: any) => sp.name)))).join(', ')
                          : '-';
                      return (
                      <tr key={job.job_id} className="border-t border-indigo-100 bg-yellow-50/70 animate-pulse">
                        <td className="p-2 text-yellow-700 font-semibold">Running</td>
                        <td className="p-2">{pageMap[job.page_id]?.name || job.page_id || '-'}</td>
                        <td className="p-2">{srcNames || '-'}</td>
                        <td className="p-2">{JOB_LABELS[job.job_type] || job.job_type}</td>
                        <td className="p-2">{job.start_time ? new Date(job.start_time).toLocaleString() : '-'}</td>
                        <td className="p-2">-</td>
                        <td className="p-2">-</td>
                        <td className="p-2"></td>
                      </tr>
                      );
                    })}
                    {waitingJobs.map(job => {
                      const srcNames = Array.isArray(job.page_names)
                        ? job.page_names.join(', ')
                        : Array.isArray(job.suggestions)
                          ? Array.from(new Set(job.suggestions.flatMap((s: any) => (s.source_pages || []).map((sp: any) => sp.name)))).join(', ')
                          : '-';
                      return (
                      <tr key={job.job_id} className="border-t border-indigo-100 bg-indigo-50/80">
                        <td className="p-2 text-fuchsia-700 font-semibold">{t('needs_review')}</td>
                        <td className="p-2">{pageMap[job.page_id]?.name || job.page_id || '-'}</td>
                        <td className="p-2">{srcNames || '-'}</td>
                        <td className="p-2">{JOB_LABELS[job.job_type] || job.job_type}</td>
                        <td className="p-2">{job.start_time ? new Date(job.start_time).toLocaleString() : '-'}</td>
                        <td className="p-2">{job.end_time ? new Date(job.end_time).toLocaleString() : '-'}</td>
                        <td className="p-2">{job.start_time && job.end_time ? Math.round((new Date(job.end_time).getTime() - new Date(job.start_time).getTime())/1000) + 's' : '-'}</td>
                        <td className="p-2 flex gap-2">
                          {job.job_type === 'analyze_pages' ? (
                            <Link className="text-fuchsia-700 underline font-bold" href={`/agent_writer/${selectedAgent.id}/suggestions/${job.job_id}`}>{t('review')}</Link>
                          ) : (
                            <Link className="text-fuchsia-700 underline font-bold" href={`/agent_writer/${selectedAgent.id}/review/${job.job_id}`}>{t('review')}</Link>
                          )}
                          <button
                            className="text-red-600 underline"
                            onClick={async () => {
                              await updateWriterJob(job.job_id, { action_needed: 'done' }, token || '');
                              mutateWriterJobs();
                            }}
                          >
                            {t('close_job')}
                          </button>
                        </td>
                      </tr>
                      );
                    })}
                    {doneJobs.map(job => {
                      const srcNames = Array.isArray(job.page_names)
                        ? job.page_names.join(', ')
                        : Array.isArray(job.suggestions)
                          ? Array.from(new Set(job.suggestions.flatMap((s: any) => (s.source_pages || []).map((sp: any) => sp.name)))).join(', ')
                          : '-';
                      return (
                      <tr key={job.job_id} className="border-t border-indigo-100 text-indigo-400 bg-white">
                        <td className="p-2">{t('done')}</td>
                        <td className="p-2">{job.page_name || pageMap[job.page_id]?.name || '-'}</td>
                        <td className="p-2">{srcNames || '-'}</td>
                        <td className="p-2">{JOB_LABELS[job.job_type] || job.job_type}</td>
                        <td className="p-2">{job.start_time ? new Date(job.start_time).toLocaleString() : '-'}</td>
                        <td className="p-2">{job.end_time ? new Date(job.end_time).toLocaleString() : '-'}</td>
                        <td className="p-2">{job.start_time && job.end_time ? Math.round((new Date(job.end_time).getTime() - new Date(job.end_time).getTime())/1000) + 's' : '-'}</td>
                        <td className="p-2"></td>
                      </tr>
                      );
                    })}
                    {runningJobs.length === 0 && waitingJobs.length === 0 && doneJobs.length === 0 && (
                      <tr><td colSpan={8} className="p-2 text-center">{t('no_jobs')}</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}

            {/* Job Feedback */}
            {jobFeedback && (
              <div className="bg-fuchsia-100 text-fuchsia-900 font-bold rounded-xl px-4 py-2 shadow mb-2 text-center">
                {jobFeedback}
              </div>
            )}

            {recentJobs.length > 0 && (
              <div className="border border-indigo-200 rounded-xl bg-white/90 shadow p-2 mb-4">
                <h3 className="font-bold mb-2 text-indigo-700">Recent Requests</h3>
                <table className="min-w-full text-sm">
                  <tbody>
                    {recentJobs.map(j => (
                      <tr key={j.job_id} className="border-t border-indigo-100">
                        <td className="p-1">{new Date(j.start_time).toLocaleString()}</td>
                        <td className="p-1">{pageMap[j.page_id]?.name || j.page_names?.[0]}</td>
                        <td className="p-1"><a className="text-fuchsia-700 underline" href={`/agent_writer/${selectedAgent.id}/history/${j.job_id}`}>View</a></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Library of Lore (Page Selector) */}
            <div>
              <div className="flex flex-col sm:flex-row items-end gap-4 mt-10 mb-2">
                <div className="flex-1">
                  <h3 className="text-xl font-bold mb-1 text-fuchsia-700">{t('library_of_lore')}</h3>
                  <p className="text-indigo-900/80 mb-2">{t('library_desc')}</p>
                </div>
                <select
                  className="px-3 py-2 rounded-xl border border-indigo-200 bg-white text-sm w-full sm:w-auto"
                  value={selectedConcept}
                  onChange={e => {
                    const val = e.target.value;
                    setSelectedConcept(val ? Number(val) : "");
                    setPageIndex(0);
                  }}
                >
                  <option value="">All concepts</option>
                  {concepts?.map(c => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
                <div className="flex items-center gap-2 bg-white border border-indigo-200 px-4 py-2 rounded-xl shadow-inner w-full sm:w-[260px]">
                  <Search className="w-5 h-5 text-indigo-400" />
                  <input
                    className="bg-transparent outline-none flex-1 text-base text-indigo-700 placeholder-indigo-400"
                    placeholder={t('search_lore_placeholder')}
                    value={search}
                    onChange={e => { setSearch(e.target.value); setPageIndex(0); }}
                  />
                </div>
                <button
                  disabled={selectedPages.length === 0}
                  onClick={async () => {
                    const res = await startAnalyzeJob(selectedAgent.id, selectedPages, token || "");
                    setJobs(j => [...j, { id: res.job_id, pages: selectedPages.map(pid => pageMap[pid]?.name || pid), status: "queued" }]);
                    setSelectedPages([]);
                    setJobFeedback(t('processing_selected_pages'));
                    setTimeout(() => setJobFeedback(null), 1200);
                  }}
                  className="px-4 py-2 rounded-xl bg-fuchsia-600 text-white font-semibold shadow hover:bg-fuchsia-800 transition text-sm disabled:opacity-50"
                >
                  <Feather className="w-4 h-4 mr-1 inline" /> {t('process_selected')}
                </button>
              </div>
              <div className="overflow-x-auto rounded-xl border border-indigo-100 shadow">
                <table className="min-w-full text-sm bg-white/80">
                  <thead>
                    <tr className="text-left text-fuchsia-800">
                      <th className="w-8"></th>
                      <th className="w-25"></th>
                      <th className="cursor-pointer" onClick={() => changeSort('name')}>Name</th>
                      <th className="cursor-pointer" onClick={() => changeSort('concept')}>Concept</th>
                      <th className="cursor-pointer" onClick={() => changeSort('autogenerated')}>AI Content?</th>
                      <th className="cursor-pointer" onClick={() => changeSort('updated_at')}>Updated</th>
                      <th className="w-48"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {paginated.map(p => (
                      <tr key={p.id} className="border-b border-indigo-50 hover:bg-indigo-50 transition">
                        <td className="px-2 text-center">
                          <input
                            type="checkbox"
                            checked={selectedPages.includes(p.id)}
                            onChange={e => {
                              setSelectedPages(sp => e.target.checked ? [...sp, p.id] : sp.filter(id => id !== p.id));
                            }}
                          />
                        </td>
                        <td className="py-2">
                          <Image src={p.logo || "/images/pages/concept/concept.png"} alt={p.name} width={48} height={48} className="w-14 h-14 rounded object-cover" />
                        </td>
                        <td className="py-2 font-semibold">{p.name}</td>
                        <td className="py-2">{conceptMap[p.concept_id]?.name || ""}</td>
                        <td className="py-2 text-center">{p.autogenerated_content ? 'Yes' : 'No'}</td>
                        <td className="py-2">{p.updated_at ? new Date(p.updated_at).toLocaleDateString() : '-'}</td>
                        <td className="py-2 px-2 text-right">
                          <button
                            className="px-3 py-2 rounded-xl bg-fuchsia-600 text-white font-semibold text-xs hover:bg-fuchsia-700 transition"
                            onClick={async () => {
                              const res = await startAnalyzeJob(selectedAgent.id, [p.id], token || "");
                              setJobs(j => [...j, { id: res.job_id, pages: [p.name], status: "queued" }]);
                              setJobFeedback(`Started job for "${p.name}"!`);
                              setTimeout(() => setJobFeedback(null), 1200);
                            }}
                          >
                            {t('start_job')}
                          </button>
                        </td>
                      </tr>
                    ))}
                    {paginated.length === 0 && (
                      <tr><td colSpan={7} className="text-center py-4">{t('no_lore_found')}</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {/* Pagination */}
              <div className="flex items-center justify-between mt-4">
                <button
                  disabled={pageIndex === 0}
                  onClick={() => setPageIndex(p => Math.max(0, p - 1))}
                  className="px-3 py-1 rounded-xl border border-fuchsia-300 text-fuchsia-700 disabled:opacity-50 font-semibold"
                >
                  {t('previous')}
                </button>
                <span className="text-sm">Page {pageIndex + 1} of {totalPages}</span>
                <button
                  disabled={pageIndex >= totalPages - 1}
                  onClick={() => setPageIndex(p => Math.min(totalPages - 1, p + 1))}
                  className="px-3 py-1 rounded-xl border border-fuchsia-300 text-fuchsia-700 disabled:opacity-50 font-semibold"
                >
                  {t('next')}
                </button>
              </div>
            </div>
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}

export default function AgentWriterPage() {
  return (
    <Suspense>
      <AgentWriterPageContent />
    </Suspense>
  );
}
