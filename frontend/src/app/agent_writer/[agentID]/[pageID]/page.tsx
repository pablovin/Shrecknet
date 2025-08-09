// FULLY UPDATED page.tsx WITH MULTI-MERGE SUPPORT
"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState, useRef } from "react";
import { useParams } from "next/navigation";
import DashboardLayout from "../../../components/DashboardLayout";
import AuthGuard from "../../../components/auth/AuthGuard";
import { useAuth } from "../../../components/auth/AuthProvider";
import { usePageById } from "../../../lib/usePageById";
import { useAgentById } from "../../../lib/useAgentById";
import {
  analyzePageWithAgent,
  generatePagesWithAgent,
} from "../../../lib/agentAPI";
import {
  updatePage,
  createPage,
  getPagesForConcept,
  getPage,
} from "../../../lib/pagesAPI";
import { usePages } from "../../../lib/usePage";
import { useConcepts } from "../../../lib/useConcept";
import { useWorld } from "../../../lib/useWorld";
import CreatePageForm from "../../../components/create_page/CreatePageForm";
import SuggestionCard from "../../../components/agents/SuggestionCard";
import Image from "next/image";
import { Loader2, AlertTriangle } from "lucide-react";
import { useTranslation } from "@/app/hooks/useTranslation";

const AGENT_PERSONALITIES = {
  "Lorekeeper Lyra":
    "“Every story is a new star in the night sky. Let’s light up your world!”",
  "Archivist Axion":
    "“Where words end, legends begin. Let us chronicle them together.”",
  default: "“I am your humble scribe. Let’s bring new tales to your world!”",
};

const STEPS = [
  { label: "Review Lore" },
  { label: "Scribe’s Suggestions" },
  { label: "Choose Legends" },
  { label: "Finalize Chronicle" },
];

function Stepper({ step }) {
  return (
    <div className="flex items-center justify-center gap-4 mb-6">
      {STEPS.map((s, idx) => (
        <div className="flex items-center gap-1" key={s.label}>
          <div
            className={`w-8 h-8 flex items-center justify-center rounded-full border-2 transition-all
            ${
              step === idx
                ? "bg-[var(--primary)] text-[var(--primary-foreground)] border-[var(--primary)] scale-110 shadow"
                : step > idx
                  ? "bg-[var(--accent)]/70 text-white border-[var(--accent)]"
                  : "bg-[var(--muted)] text-gray-400 border-gray-300"
            } font-bold`}
          >
            {idx + 1}
          </div>
          <div
            className={`text-xs font-semibold mt-2 text-center ${step === idx ? "text-[var(--primary)]" : "text-gray-400"}`}
          >
            {s.label}
          </div>
          {idx < STEPS.length - 1 && (
            <span className="w-6 h-1 bg-gradient-to-r from-[var(--primary)]/40 to-[var(--accent)]/30 rounded"></span>
          )}
        </div>
      ))}
    </div>
  );
}

function AgentBubble({ agent, children, loading = false }) {
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

export default function PageAnalyze() {
  const { t } = useTranslation();
  const { agentID, pageID } = useParams();
  const { user, token } = useAuth();
  const { page } = usePageById(Number(pageID));
  const { agent } = useAgentById(Number(agentID));
  const { concepts } = useConcepts(agent?.world_id);
  const { world } = useWorld(agent?.world_id);
  const { pages: allPages } = usePages(
    agent?.world_id ? { gameworld_id: agent.world_id } : {},
  );

  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [selectedSuggestions, setSelectedSuggestions] = useState([]);
  const [generatedPages, setGeneratedPages] = useState([]);
  const [activeGen, setActiveGen] = useState(0);
  const suggestionRefs = useRef([]);

  const scrollToCard = (idx) => {
    const ref = suggestionRefs.current[idx];
    if (ref && ref.scrollIntoView)
      ref.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const getPersonality = () =>
    AGENT_PERSONALITIES[agent?.name] || AGENT_PERSONALITIES.default;

  async function handleAnalyze() {
    if (!agentID || !pageID || !token) return;
    setLoading(true);
    try {
      const data = await analyzePageWithAgent(
        Number(agentID),
        Number(pageID),
        token,
      );
      let suggs = data.suggestions || [];
      suggs = await Promise.all(
        suggs.map(async (s) => {
          if (s.exists) {
            const pages = await getPagesForConcept(s.concept_id, token);
            const existing = pages.find(
              (p) => p.name.toLowerCase() === s.name.toLowerCase(),
            );
            if (existing) {
              s.target_page_id = existing.id;
              s.mode = "update";
              s.concept_id = existing.concept_id;
              const c = concepts?.find((c) => c.id === existing.concept_id);
              if (c) s.concept = c.name;
            }
          }
          return s;
        }),
      );
      suggs.sort((a, b) => (a.exists === b.exists ? 0 : a.exists ? -1 : 1));
      setSelectedSuggestions(suggs);
      setStep(2);
    } catch (err) {
      console.error(err);
      alert(t("fail_analyze_page"));
    }
    setLoading(false);
  }

  function buildMergeGroups(suggestions) {
    const graph = new Map();

    suggestions.forEach((s) => {
      if (!graph.has(s.name)) graph.set(s.name, new Set());
      (s.merge_targets || []).forEach((target) => {
        graph.get(s.name).add(target);
        if (!graph.has(target)) graph.set(target, new Set());
        graph.get(target).add(s.name);
      });
    });

    const visited = new Set();
    const groups = [];

    for (const name of graph.keys()) {
      if (visited.has(name)) continue;
      const queue = [name];
      const group = new Set();
      while (queue.length) {
        const current = queue.pop();
        if (visited.has(current)) continue;
        visited.add(current);
        group.add(current);
        graph.get(current).forEach((n) => queue.push(n));
      }
      groups.push(Array.from(group));
    }

    const independent = suggestions
      .filter((s) => !graph.has(s.name))
      .map((s) => [s.name]);
    return [...groups, ...independent];
  }

  async function handleGenerate({ bulkAcceptUpdates = false } = {}) {
    if (!agentID || !pageID || !token) return;
    setLoading(true);
    try {
      const mergeGroups = buildMergeGroups(selectedSuggestions);

      const fullGroups = mergeGroups.map((names) => {
        const group = selectedSuggestions.filter((s) => names.includes(s.name));
        const base = group.find((s) => s.merge_targets?.length > 0) || group[0];
        const mergedItems = group.filter((s) => s !== base);
        return { base, mergedItems, group };
      });

      const pageSpecs = fullGroups.map(({ group }) => {
        const base =
          group.find(
            (g) => Array.isArray(g.merge_targets) && g.merge_targets.length > 0,
          ) || group[0];
        const sourceIds = Array.from(
          new Set(
            group.flatMap((g) =>
              (g.source_pages || []).map((sp: any) => sp.id),
            ),
          ),
        );
        const spec: any = {
          name: base.name,
          concept_id: base.concept_id,
          source_page_ids: sourceIds,
        };
        const mode = base.mode || (base.exists ? "update" : "create");
        spec.mode = mode;
        if (mode === "update" && base.target_page_id) {
          spec.target_page_id = base.target_page_id;
        }
        return spec;
      });

      const generatedAll = await generatePagesWithAgent(
        Number(agentID),
        Number(pageID),
        pageSpecs,
        token,
        mergeGroups,
      );
      const generatedPagesMap = new Map(
        (generatedAll.pages || []).map((p) => [p.name, p]),
      );

      const mergedResult = [];

      for (const { base, mergedItems } of fullGroups) {
        const generatedBase = generatedPagesMap.get(base.name);
        const mergedContents = [
          generatedBase?.autogenerated_content ||
            base.autogenerated_content ||
            "",
        ];

        for (const item of mergedItems) {
          const generated = generatedPagesMap.get(item.name);
          if (generated?.autogenerated_content) {
            mergedContents.push(generated.autogenerated_content);
          } else if (item.autogenerated_content) {
            mergedContents.push(item.autogenerated_content);
          }
        }

        const combinedAutogen = mergedContents
          .filter(Boolean)
          .join("\n\n---\n\n");

        if ((base.exists || base.mode === "update") && bulkAcceptUpdates) {
          const backendPage = base.target_page_id
            ? await getPage(base.target_page_id, token)
            : (await getPagesForConcept(base.concept_id, token)).find(
                (p) => p.name.toLowerCase() === base.name.toLowerCase(),
              );

          const previousAutogen = backendPage?.autogenerated_content || "";
          const extraHeader = `<h2>Notes from ${page?.name || "Analysis"}</h2>`;
          const fullAutogen = `${previousAutogen ? previousAutogen + "\n\n---\n\n" : ""}${extraHeader}\n${combinedAutogen}`;

          await updatePage(
            backendPage.id,
            {
              name: backendPage.name,
              logo: backendPage.logo || undefined,
              autogenerated_content: fullAutogen,
              updated_by_agent_id: Number(agentID),
              allow_crosslinks: backendPage.allowCrosslinks,
              ignore_crosslink: backendPage.ignoreCrosslink,
              allow_crossworld: backendPage.allowCrossworld,
            },
            token,
          );
        } else {
          if (base.exists || base.mode === "update") {
            const backendPage = base.target_page_id
              ? await getPage(base.target_page_id, token)
              : (await getPagesForConcept(base.concept_id, token)).find(
                  (p) => p.name.toLowerCase() === base.name.toLowerCase(),
                );

            const previousAutogen = backendPage?.autogenerated_content || "";
            const extraHeader = `<h2>Notes from ${page?.name || "Analysis"}</h2>`;
            const fullAutogen = `${previousAutogen ? previousAutogen + "\n\n---\n\n" : ""}${extraHeader}\n${combinedAutogen}`;

            mergedResult.push({
              ...backendPage,
              autogenerated_content: fullAutogen,
            });
          } else {
            const created = generatedPagesMap.get(base.name);
            if (created) {
              mergedResult.push({
                ...created,
                autogenerated_content: combinedAutogen,
              });
            }
          }
        }
      }

      setGeneratedPages(mergedResult);
      setActiveGen(0);
      setStep(3);
    } catch (err) {
      console.error(err);
      alert(t("fail_generate_pages"));
    }
    setLoading(false);
  }

  const [subStep, setSubStep] = useState<"a" | "b" | "c">("a");
  const [bulkAcceptUpdates, setBulkAcceptUpdates] = useState(false);

  function advanceSubStep() {
    if (subStep === "a") setSubStep("b");
    else if (subStep === "b") setSubStep("c");
    else setStep(3);
  }

  function goBackSubStep() {
    if (subStep === "c") setSubStep("b");
    else if (subStep === "b") setSubStep("a");
    else setStep(0);
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-2 sm:px-6 py-10 flex justify-center">
          <div className="">
            {agent && (
              <div className="flex flex-col items-center gap-2 mb-3">
                <div className="flex items-center gap-4">
                  <Image
                    src={agent.logo || "/images/default/avatars/logo.png"}
                    alt={agent.name}
                    width={64}
                    height={64}
                    className="rounded-full border-2 border-[var(--primary)] shadow-lg"
                  />
                  <div>
                    <h1 className="text-2xl font-extrabold text-[var(--primary)]">
                      {agent.name}, Scribe of {world?.name}
                    </h1>
                    <div className="italic text-md text-[var(--accent)]">
                      {getPersonality()}
                    </div>
                  </div>
                </div>
                <div className="w-full mt-6">
                  <Stepper step={step} />
                </div>
              </div>
            )}

            {step === 0 && (
              <>
                <AgentBubble agent={agent}>
                  <b>{t("step1_review_tome")}</b>
                  <br />
                  {user?.nickname
                    ? `${user.nickname}, ${t("lets_read_page")}`
                    : `“${t("lets_read_page")}”`}
                </AgentBubble>
                {page && (
                  <div className="bg-[var(--card)] p-6 rounded-xl border border-[var(--border)] shadow max-w-3xl mx-auto">
                    <h2 className="text-lg font-bold mb-2">{page.name}</h2>
                    <div
                      className="prose prose-invert max-w-none"
                      dangerouslySetInnerHTML={{ __html: page.content }}
                    />
                  </div>
                )}
                <div className="flex justify-end mt-6">
                  <button
                    onClick={handleAnalyze}
                    disabled={loading}
                    className="px-4 py-2 rounded-xl bg-[var(--primary)] text-[var(--primary-foreground)] font-bold shadow hover:bg-[var(--accent)] transition disabled:opacity-50"
                  >
                    {loading ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />{" "}
                        {t("reading")}
                      </>
                    ) : (
                      t("ready_scribe")
                    )}
                  </button>
                </div>
              </>
            )}

            {/* // STEP 2A/2B/2C FLOW LOGIC AND UI */}

            {step === 2 && (
              <>
                {/* Agent Bubble for Substeps */}
                <div className="w-full max-w-screen-2xl mx-auto px-4 mb-4">
                  <AgentBubble agent={agent}>
                    {subStep === "a" && (
                      <>
                        <b>{t("step2a_remove")}</b>
                        <br />
                        {user?.nickname
                          ? `${user.nickname}, ${t("step2a_desc")}`
                          : t("step2a_desc")}
                        <p className="mt-2 text-sm italic text-[var(--muted-foreground)]">
                          {t("step2a_hint")}
                        </p>
                      </>
                    )}
                    {subStep === "b" && (
                      <>
                        <b>{t("step2b_merge")}</b>
                        <br />
                        {user?.nickname
                          ? `${user.nickname}, ${t("step2b_desc")}`
                          : t("step2b_desc")}
                        <p className="mt-2 text-sm italic text-[var(--muted-foreground)]">
                          {t("step2b_hint")}
                        </p>
                      </>
                    )}
                    {subStep === "c" && (
                      <>
                        <b>{t("step2c_finalize")}</b>
                        <br />
                        {user?.nickname
                          ? `${user.nickname}, ${t("step2c_desc")}`
                          : t("step2c_desc")}
                        <p className="mt-2 text-sm italic text-[var(--muted-foreground)]">
                          {t("step2c_hint")}
                        </p>
                      </>
                    )}
                  </AgentBubble>
                </div>

                {/* Main Grid Layout */}
                <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-4 flex justify-center">
                  <div className="w-full max-w-screen-2xl grid grid-cols-1 xl:grid-cols-[1fr_18rem] gap-6 items-start">
                    {/* Suggestion Cards */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                      {selectedSuggestions.map((sugg, idx) => (
                        <div
                          key={idx}
                          ref={(el) => (suggestionRefs.current[idx] = el)}
                        >
                          <SuggestionCard
                            index={idx}
                            suggestion={sugg}
                            suggestions={selectedSuggestions}
                            concepts={concepts}
                            pages={allPages}
                            token={token}
                            subStep={subStep}
                            onUpdate={(i, data) => {
                              const copy = [...selectedSuggestions];
                              copy[i] = { ...copy[i], ...data };
                              setSelectedSuggestions(copy);
                            }}
                            onRemove={(i) => {
                              const copy = [...selectedSuggestions];
                              copy.splice(i, 1);
                              setSelectedSuggestions(copy);
                            }}
                          />
                        </div>
                      ))}
                    </div>

                    {/* Summary Panel */}
                    <div className="sticky top-10 bg-[var(--surface)] border border-[var(--border)] rounded-lg p-4">
                      <h2 className="text-xl font-bold mb-3 text-[var(--primary)] border-b border-[var(--primary)] pb-1">
                        📋 {t("summary")}
                      </h2>
                      <p className="text-sm mb-4 text-[var(--muted-foreground)]">
                        {selectedSuggestions.length} {t("suggestions_label")}
                      </p>
                      <p className="mb-4">{t("track_progress")}</p>
                      <div className="grid grid-cols-2 gap-2 text-sm">
                        {selectedSuggestions.map((s, idx) => {
                          const isMerged = selectedSuggestions.some(
                            (other, j) =>
                              j !== idx &&
                              Array.isArray(other.merge_targets) &&
                              other.merge_targets.includes(s.name),
                          );
                          const type = isMerged
                            ? "Merged"
                            : s.mode === "update" || s.exists
                              ? "Update"
                              : "Create";
                          const color =
                            type === "Update"
                              ? "text-blue-500"
                              : type === "Create"
                                ? "text-green-500"
                                : "text-gray-500";
                          return (
                            <div
                              key={idx}
                              onClick={() => scrollToCard(idx)}
                              className="cursor-pointer px-3 py-2 rounded border border-[var(--border)] bg-[var(--surface-variant)] hover:bg-[var(--surface)] transition"
                            >
                              <div className={`font-semibold ${color}`}>
                                {s.name}
                              </div>
                              <div className="text-xs text-[var(--muted-foreground)]">
                                {type} in {s.concept || "(No Concept)"}
                              </div>
                              {!s.concept && (
                                <div className="text-red-500 text-xs flex items-center gap-1 mt-1">
                                  <AlertTriangle className="w-3 h-3" />{" "}
                                  {t("missing_concept")}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Step Controls */}
                <div className="flex justify-between mt-8 w-full max-w-screen-2xl mx-auto px-4">
                  <button
                    onClick={goBackSubStep}
                    className="px-4 py-2 rounded-xl border border-[var(--primary)] text-[var(--primary)] font-bold shadow hover:bg-[var(--accent)]/30 transition"
                  >
                    {t("back")}
                  </button>

                  {step === 2 && subStep === "c" && (
                    <div className="max-w-screen-2xl mx-auto px-4 mb-4">
                      <label className="flex items-center gap-2 text-sm font-medium text-[var(--foreground)]">
                        <input
                          type="checkbox"
                          checked={bulkAcceptUpdates}
                          onChange={(e) =>
                            setBulkAcceptUpdates(e.target.checked)
                          }
                          className="accent-[var(--primary)]"
                        />
                        {t("auto_apply_updates")}
                      </label>
                      <p className="text-xs text-[var(--muted-foreground)] ml-6 mt-1">
                        {t("auto_apply_updates_desc")}
                      </p>
                    </div>
                  )}
                  {subStep !== "c" ? (
                    <button
                      onClick={advanceSubStep}
                      className="px-4 py-2 rounded-xl bg-[var(--primary)] text-white font-bold shadow hover:bg-[var(--accent)] transition"
                    >
                      {t("continue_to_step")} {subStep === "a" ? "2B" : "2C"}
                    </button>
                  ) : (
                    <button
                      onClick={() => handleGenerate({ bulkAcceptUpdates })}
                      disabled={loading}
                      className="px-4 py-2 rounded-xl bg-[var(--primary)] text-white font-bold shadow hover:bg-[var(--accent)] transition disabled:opacity-50"
                    >
                      {loading ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />{" "}
                          {t("processing")}
                        </>
                      ) : (
                        t("inscribe_legends")
                      )}
                    </button>
                  )}
                </div>
              </>
            )}

            {step === 3 && generatedPages.length > 0 && (
              <>
                <AgentBubble agent={agent}>
                  <b>{t("final_step_review")}</b>
                  <br />
                  {t("final_step_desc")}
                </AgentBubble>

                <div className="w-full max-w-screen-2xl mx-auto px-4">
                  <div className="overflow-x-auto">
                    <div className="flex flex-wrap gap-2 border-b border-[var(--border)] pb-2 mb-6">
                      {generatedPages.map((p, idx) => (
                        <button
                          key={idx}
                          onClick={() => setActiveGen(idx)}
                          className={`px-4 py-2 rounded-t-lg text-sm font-bold border-b-2 transition-all ${
                            activeGen === idx
                              ? "border-[var(--primary)] text-[var(--primary)] bg-[var(--card)]"
                              : "border-transparent text-[var(--muted-foreground)] hover:text-[var(--primary)]"
                          }`}
                        >
                          {p.name}
                        </button>
                      ))}
                    </div>
                  </div>

                  {generatedPages.map(
                    (p, idx) =>
                      activeGen === idx && (
                        <div
                          key={idx}
                          className="border border-[var(--border)] rounded-xl p-6 bg-[var(--card)] shadow-sm mb-12"
                        >
                          {/* Concept Header */}
                          <div className="flex items-center gap-4 mb-4">
                            {concepts?.find((c) => c.id === p.concept_id)
                              ?.logo && (
                              <img
                                src={
                                  concepts.find((c) => c.id === p.concept_id)
                                    .logo
                                }
                                alt="concept logo"
                                className="w-10 h-10 rounded-full border border-[var(--border)]"
                              />
                            )}
                            <h3 className="text-xl font-bold text-[var(--primary)]">
                              {concepts?.find((c) => c.id === p.concept_id)
                                ?.name || t("unknown_concept")}
                            </h3>
                          </div>

                          {/* Autogenerated Content */}
                          <div className="prose prose-invert max-w-none mb-6 border-l-4 border-yellow-400 pl-4">
                            <div
                              dangerouslySetInnerHTML={{
                                __html: p.autogenerated_content,
                              }}
                            />
                          </div>

                          {/* Create/Edit Page Form */}
                          <CreatePageForm
                            selectedWorld={world}
                            selectedConcept={concepts?.find(
                              (c) => c.id === p.concept_id,
                            )}
                            token={token}
                            initialValues={p}
                            mode={p?.id ? "edit" : "create"}
                            onSubmit={async (payload) => {
                              if (!token) return;
                              if (p?.id) {
                                await updatePage(
                                  p.id,
                                  {
                                    ...payload,
                                    autogenerated_content:
                                      p.autogenerated_content,
                                    updated_by_agent_id: Number(agentID),
                                  },
                                  token,
                                );
                              } else {
                                const created = await createPage(
                                  payload,
                                  token,
                                );
                                await updatePage(
                                  created.id,
                                  {
                                    autogenerated_content:
                                      p.autogenerated_content,
                                    updated_by_agent_id: Number(agentID),
                                  },
                                  token,
                                );
                              }
                              setGeneratedPages((g) =>
                                g.filter((_, i) => i !== idx),
                              );
                            }}
                          />
                        </div>
                      ),
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
