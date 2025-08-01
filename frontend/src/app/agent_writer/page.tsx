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
import { startAnalyzeJob } from "../lib/agentAPI";
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
  const { jobs: writerJobs } = useWriterJobs();
  const [jobFeedback, setJobFeedback] = useState<string | null>(null);

  useEffect(() => {
    if (selectedAgent || agents.length === 0) return;
    const param = searchParams.get("agent");
    if (param) {
      const ag = agents.find((a) => a.id === Number(param));
      if (ag) setSelectedAgent(ag);
    }
  }, [agents, searchParams, selectedAgent]);


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


            {/* Job Feedback */}
            {jobFeedback && (
              <div className="bg-fuchsia-100 text-fuchsia-900 font-bold rounded-xl px-4 py-2 shadow mb-2 text-center">
                {jobFeedback}
              </div>
            )}

            {recentJobs.length > 0 && (
              <div className="border border-indigo-200 rounded-xl bg-white/90 shadow p-2 mb-4">
                <h3 className="font-bold mb-2 text-indigo-700">Recent Requests</h3>
                <div>
                  {recentJobs.map(j => (
                    <details key={j.job_id} className="border-t border-indigo-100">
                      <summary className="cursor-pointer flex justify-between p-1">
                        <span>{new Date(j.start_time).toLocaleString()}</span>
                        <span>{pageMap[j.page_id]?.name || j.page_names?.[0]}</span>
                      </summary>
                      <div className="p-2">
                        <table className="min-w-full text-sm">
                          <thead>
                            <tr className="text-left text-indigo-800">
                              <th className="p-1">Type</th>
                              <th className="p-1">Started</th>
                              <th className="p-1">Ended</th>
                              <th className="p-1">Duration</th>
                              <th className="p-1">Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr>
                              <td className="p-1">{JOB_LABELS[j.job_type] || j.job_type}</td>
                              <td className="p-1">{j.start_time ? new Date(j.start_time).toLocaleString() : '-'}</td>
                              <td className="p-1">{j.end_time ? new Date(j.end_time).toLocaleString() : '-'}</td>
                              <td className="p-1">{j.start_time && j.end_time ? Math.round((new Date(j.end_time).getTime() - new Date(j.start_time).getTime())/1000) + 's' : '-'}</td>
                              <td className="p-1 capitalize">{j.status}</td>
                            </tr>
                          </tbody>
                        </table>
                        <div className="mt-2 flex gap-2">
                          {j.status === 'done' && j.action_needed === 'review' && (
                            <Link
                              className="text-fuchsia-700 underline font-bold"
                              href={`/agent_writer/${selectedAgent.id}/${j.job_type === 'analyze_pages' ? 'suggestions' : 'review'}/${j.job_id}`}
                            >
                              Review
                            </Link>
                          )}
                          <Link className="text-fuchsia-700 underline" href={`/agent_writer/${selectedAgent.id}/history/${j.job_id}`}>View</Link>
                        </div>
                      </div>
                    </details>
                  ))}
                </div>
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
                    await startAnalyzeJob(selectedAgent.id, selectedPages, token || "");
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
                              await startAnalyzeJob(selectedAgent.id, [p.id], token || "");
                              setJobFeedback(`Started job for \"${p.name}\"!`);
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
