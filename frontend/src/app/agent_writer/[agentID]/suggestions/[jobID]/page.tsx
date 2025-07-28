"use client";
import { useParams, useRouter } from "next/navigation";
import DashboardLayout from "@/app/components/DashboardLayout";
import AuthGuard from "@/app/components/auth/AuthGuard";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import SuggestionCard from "@/app/components/agents/SuggestionCard";
import { getWriterJob, startGenerateJob, updateWriterJob } from "@/app/lib/agentAPI";
import { useAgentById } from "@/app/lib/useAgentById";
import { useConcepts } from "@/app/lib/useConcept";
import { useWorld } from "@/app/lib/useWorld";
import { usePages } from "@/app/lib/usePage";
import { Loader2, AlertTriangle } from "lucide-react";
import { useTranslation } from "@/app/hooks/useTranslation";

const STEPS = [
  { label: "Review Lore" },
  { label: "Scribe’s Suggestions" },
  { label: "Choose Legends" },
  { label: "Finalize Chronicle" },
];

function Stepper({ step }: { step: number }) {
  return (
    <div className="flex items-center justify-center gap-4 mb-6">
      {STEPS.map((s, idx) => (
        <div className="flex items-center gap-1" key={s.label}>
          <div
            className={`w-8 h-8 flex items-center justify-center rounded-full border-2 transition-all ${
              step === idx
                ? "bg-[var(--primary)] text-[var(--primary-foreground)] border-[var(--primary)] scale-110 shadow"
                : step > idx
                ? "bg-[var(--accent)]/70 text-white border-[var(--accent)]"
                : "bg-[var(--muted)] text-gray-400 border-gray-300"
            } font-bold`}
          >
            {idx + 1}
          </div>
          <div className={`text-xs font-semibold mt-2 text-center ${step === idx ? "text-[var(--primary)]" : "text-gray-400"}`}>{s.label}</div>
          {idx < STEPS.length - 1 && <span className="w-6 h-1 bg-gradient-to-r from-[var(--primary)]/40 to-[var(--accent)]/30 rounded"></span>}
        </div>
      ))}
    </div>
  );
}

function AgentBubble({ agent, children, loading = false }: any) {
  if (!agent) return null;
  return (
    <div className="flex items-start gap-4 mb-4">
      <div className="relative">
        <Image
          src={agent.logo || "/images/default/avatars/logo.png"}
          alt={agent.name}
          width={56}
          height={56}
          className={`rounded-full object-cover border-2 border-[var(--primary)] shadow-md ${loading ? "animate-pulse" : ""}`}
        />
        {loading && (
          <span className="absolute -bottom-1 -right-1 bg-[var(--primary)] rounded-full w-4 h-4 flex items-center justify-center">
            <Loader2 className="w-3 h-3 text-white animate-spin" />
          </span>
        )}
      </div>
      <div className="relative">
        <div className="bg-[var(--card)] border border-[var(--primary)] rounded-2xl px-4 py-3 shadow-sm text-[var(--foreground)] max-w-lg text-md">
          <span className="block">{children}</span>
        </div>
        <div className="absolute left-2 -bottom-3 w-4 h-4 bg-[var(--card)] border-l border-b border-[var(--primary)] rounded-bl-full"></div>
      </div>
    </div>
  );
}

export default function SuggestionsPage() {
  const { t } = useTranslation();
  const { agentID, jobID } = useParams();
  const { user, token } = useAuth();
  const router = useRouter();

  const [job, setJob] = useState<any>(null);
  const [selectedSuggestions, setSelectedSuggestions] = useState<any[]>([]);
  const [subStep, setSubStep] = useState<'a' | 'b' | 'c'>('a');
  const [loading, setLoading] = useState(false);

  const { agent } = useAgentById(Number(agentID));
  const { concepts } = useConcepts(agent?.world_id);
  const { world } = useWorld(agent?.world_id);
  const { pages: allPages } = usePages(agent?.world_id ? { gameworld_id: agent.world_id } : {});

  const suggestionRefs = useRef<HTMLDivElement[]>([]);

  useEffect(() => {
    if (!jobID || !token) return;
    getWriterJob(jobID as string, token)
      .then((data) => {
        setJob(data);
        const mapped = (data.suggestions || []).map((s: any) =>
          s.exists && !s.mode ? { ...s, mode: "update" } : s
        );
        setSelectedSuggestions(mapped);
      })
      .catch(() => {});
  }, [jobID, token]);

  const scrollToCard = (idx: number) => {
    const ref = suggestionRefs.current[idx];
    if (ref && ref.scrollIntoView) ref.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  function buildMergeGroups(suggestions: any[]) {
    const graph = new Map<string, Set<string>>();
    suggestions.forEach((s) => {
      if (!graph.has(s.name)) graph.set(s.name, new Set());
      (s.merge_targets || []).forEach((t: string) => {
        graph.get(s.name)!.add(t);
        if (!graph.has(t)) graph.set(t, new Set());
        graph.get(t)!.add(s.name);
      });
    });
    const visited = new Set<string>();
    const groups: string[][] = [];
    for (const name of graph.keys()) {
      if (visited.has(name)) continue;
      const queue = [name];
      const group = new Set<string>();
      while (queue.length) {
        const cur = queue.pop()!;
        if (visited.has(cur)) continue;
        visited.add(cur);
        group.add(cur);
        graph.get(cur)!.forEach((n) => queue.push(n));
      }
      groups.push(Array.from(group));
    }
    const independent = suggestions.filter((s) => !graph.has(s.name)).map((s) => [s.name]);
    return [...groups, ...independent];
  }

  const advanceSubStep = () => {
    if (subStep === 'a') setSubStep('b');
    else if (subStep === 'b') setSubStep('c');
  };

  const goBackSubStep = () => {
    if (subStep === 'c') setSubStep('b');
    else if (subStep === 'b') setSubStep('a');
  };

  async function handleGenerate() {
    if (!agentID || !job) return;
    setLoading(true);
    try {
      const mergeGroups = buildMergeGroups(selectedSuggestions);

      // prepare suggestions with merged source pages
      const prepared = selectedSuggestions.map((s) => ({ ...s }));
      for (const names of mergeGroups) {
        const group = names
          .map((n) => prepared.find((ss) => ss.name === n))
          .filter(Boolean) as any[];
        if (group.length === 0) continue;
        const sourcesMap: Record<number, any> = {};
        group.forEach((g) => {
          (g.source_pages || []).forEach((sp: any) => {
            sourcesMap[sp.id] = sp;
          });
        });
        const base =
          group.find((g) => Array.isArray(g.merge_targets) && g.merge_targets.length > 0) ||
          group[0];
        base.source_pages = Object.values(sourcesMap);
      }

      const allPages = mergeGroups.map((names) => {
        const group = names
          .map((n) => prepared.find((ss) => ss.name === n))
          .filter(Boolean) as any[];
        const base =
          group.find((g) => Array.isArray(g.merge_targets) && g.merge_targets.length > 0) ||
          group[0];
        const ids = Array.from(
          new Set(
            group.flatMap((g) => (g.source_pages || []).map((sp: any) => sp.id))
          )
        );
        return { name: base.name, concept_id: base.concept_id, source_page_ids: ids };
      });
      const basePageId = job.page_id || (Array.isArray(job.page_ids) ? job.page_ids[0] : 0);
      const res = await startGenerateJob(
        Number(agentID),
        basePageId,
        allPages as any[],
        token || "",
        prepared,
        mergeGroups,
        jobID as string
      );
      if (jobID)
        await updateWriterJob(jobID as string, { action_needed: "done" }, token || "");
      // router.push(`/agent_writer/${agentID}/review/${res.job_id}`);
      router.push(`/agent_writer?agent=${agentID}`);
    } catch (err) {
      console.error(err);
      alert("Failed to start generation");
    }
    setLoading(false);
  }

  if (!job) return (
    <AuthGuard>
      <DashboardLayout>Loading...</DashboardLayout>
    </AuthGuard>
  );

  const step = 2;

  const sortedSuggestions = [...selectedSuggestions].sort((a, b) =>
    a.name.localeCompare(b.name)
  );

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-2 sm:px-6 py-10 flex justify-center">
          <div className="w-full">
            {agent && (
              <div className="flex flex-col items-center gap-2 mb-3">
                <div className="flex items-center gap-4">
                  <Image src={agent.logo || "/images/default/avatars/logo.png"} alt={agent.name} width={64} height={64} className="rounded-full border-2 border-[var(--primary)] shadow-lg" />
                  <div>
                    <h1 className="text-2xl font-extrabold text-[var(--primary)]">{agent.name}, Scribe of {world?.name}</h1>
                  </div>
                </div>
                <div className="w-full mt-6"><Stepper step={step} /></div>
              </div>
            )}

            {/* Substep bubble */}
            <div className="w-full max-w-screen-2xl mx-auto px-4 mb-4">
              <AgentBubble agent={agent}>
                {subStep === 'a' && (
                  <>
                    <b>{t('step2a_remove')}</b>
                    <br />
                    {user?.nickname
                      ? `${user.nickname}, ${t('step2a_desc')}`
                      : t('step2a_desc')}
                    <p className="mt-2 text-sm italic text-[var(--muted-foreground)]">
                      {t('step2a_hint')}
                    </p>
                  </>
                )}
                {subStep === 'b' && (
                  <>
                    <b>{t('step2b_merge')}</b>
                    <br />
                    {user?.nickname
                      ? `${user.nickname}, ${t('step2b_desc')}`
                      : t('step2b_desc')}
                    <p className="mt-2 text-sm italic text-[var(--muted-foreground)]">
                      {t('step2b_hint')}
                    </p>
                  </>
                )}
                {subStep === 'c' && (
                  <>
                    <b>{t('step2c_finalize')}</b>
                    <br />
                    {user?.nickname
                      ? `${user.nickname}, ${t('step2c_desc')}`
                      : t('step2c_desc')}
                    <p className="mt-2 text-sm italic text-[var(--muted-foreground)]">
                      {t('step2c_hint')}
                    </p>
                  </>
                )}
              </AgentBubble>
            </div>

            <div className="w-full max-w-screen-2xl grid grid-cols-1 xl:grid-cols-[1fr_18rem] gap-6 items-start px-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                {sortedSuggestions.map((sugg, idx) => {
                  const origIndex = selectedSuggestions.indexOf(sugg);
                  return (
                  <div key={idx} ref={(el) => (suggestionRefs.current[idx] = el!)}>
                    <SuggestionCard
                      index={origIndex}
                      suggestion={sugg}
                      suggestions={sortedSuggestions}
                      concepts={concepts}
                      pages={allPages}
                      token={token}
                      subStep={subStep}
                      onUpdate={(i: number, data: any) => {
                        const copy = [...selectedSuggestions];
                        copy[i] = { ...copy[i], ...data };
                        setSelectedSuggestions(copy);
                      }}
                      onRemove={(i: number) => {
                        const copy = [...selectedSuggestions];
                        copy.splice(i, 1);
                        setSelectedSuggestions(copy);
                      }}
                    />
                  </div>
                )})}
              </div>

              <div className="sticky top-10 bg-[var(--surface)] border border-[var(--border)] rounded-lg p-4">
                <h2 className="text-xl font-bold mb-3 text-[var(--primary)] border-b border-[var(--primary)] pb-1">📋 {t('summary')}</h2>
                <p className="text-sm mb-4 text-[var(--muted-foreground)]">{selectedSuggestions.length} {t('suggestions_label')}</p>
                <p className="mb-4">{t('track_progress')}</p>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  {sortedSuggestions.map((s, idx) => {
                    const isMerged = selectedSuggestions.some(
                      (other) =>
                        other !== s &&
                        Array.isArray(other.merge_targets) &&
                        other.merge_targets.includes(s.name)
                    );
                    const type = isMerged ? "Merged" : s.mode === "update" || s.exists ? "Update" : "Create";
                    const color = type === "Update" ? "text-blue-500" : type === "Create" ? "text-green-500" : "text-gray-500";
                    return (
                      <div
                        key={idx}
                        onClick={() => scrollToCard(idx)}
                        className="cursor-pointer px-3 py-2 rounded border border-[var(--border)] bg-[var(--surface-variant)] hover:bg-[var(--surface)] transition"
                      >
                        <div className={`font-semibold ${color}`}>{s.name}</div>
                        <div className="text-xs text-[var(--muted-foreground)]">{type} in {s.concept || "(No Concept)"}</div>
                        {!s.concept && (
                          <div className="text-red-500 text-xs flex items-center gap-1 mt-1">
                            <AlertTriangle className="w-3 h-3" /> {t('missing_concept')}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="flex justify-between mt-8 w-full max-w-screen-2xl mx-auto px-4">
              <button
                onClick={goBackSubStep}
                className="px-4 py-2 rounded-xl border border-[var(--primary)] text-[var(--primary)] font-bold shadow hover:bg-[var(--accent)]/30 transition"
              >
                {t('back')}
              </button>


              {subStep !== 'c' ? (
                <button
                  onClick={advanceSubStep}
                  className="px-4 py-2 rounded-xl bg-[var(--primary)] text-white font-bold shadow hover:bg-[var(--accent)] transition"
                >
                  {t('continue_to_step')} {subStep === 'a' ? '2B' : '2C'}
                </button>
              ) : (
                <button
                  onClick={handleGenerate}
                  disabled={loading}
                  className="px-4 py-2 rounded-xl bg-[var(--primary)] text-white font-bold shadow hover:bg-[var(--accent)] transition disabled:opacity-50"
                >
                  {loading ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" /> {t('processing')}
                    </>
                  ) : (
                    t('inscribe_legends')
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
