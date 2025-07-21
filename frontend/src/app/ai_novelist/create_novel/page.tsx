"use client";
export const dynamic = "force-dynamic";
import DashboardLayout from "../../components/DashboardLayout";
import AuthGuard from "../../components/auth/AuthGuard";
import { useAuth } from "../../components/auth/AuthProvider";
import useRoleRedirect from "../../hooks/useRoleRedirect";
import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useEffect, useState, useMemo, useRef } from "react";
import { useAgentById } from "../../lib/useAgentById";
import { useAgents } from "../../lib/useAgents";
import { usePages } from "../../lib/usePage";
import { startNovelJob, updateNovelistJob } from "../../lib/agentAPI";
import { useNovelistJobs } from "../../lib/useNovelistJobs";
import RichEditor from "../../components/editor/RichEditor";
import Image from "next/image";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { X, Search, BookOpen } from "lucide-react";
import { useTranslation } from "../../hooks/useTranslation";

const AGENT_PERSONALITIES: Record<string, string> = {
  "Lorekeeper Lyra": "“A tale untold is a world unseen. Let’s weave a new legend!”",
  "Archivist Axion": "“Every saga begins with a spark. Ready when you are!”",
  "Chronicle": "“Words are the threads of fate. Let me spin them for you.”",
  default: "“I am here to craft stories from your world’s lore.”",
};

function NovelistJobStatus({ agentId, jobs }: { agentId: number; jobs: any[] }) {
  const agentJobs = jobs.filter(j => j.agent_id === agentId);
  if (agentJobs.length === 0) return null;

  const running = agentJobs.filter(j => j.status !== "done");
  const waiting = agentJobs.filter(j => j.status === "done" && j.action_needed === "review");
  const recent = agentJobs
    .filter(j => j.status === "done" && j.action_needed !== "review")
    .sort((a, b) => new Date(b.end_time || b.start_time || 0).getTime() - new Date(a.end_time || a.start_time || 0).getTime())
    .slice(0, 3);

  function Row({ job }: { job: any }) {
    const { token } = useAuth();
    const { mutate } = useNovelistJobs();
    const { t } = useTranslation();
    const duration = job.start_time && job.end_time
      ? Math.round((new Date(job.end_time).getTime() - new Date(job.start_time).getTime()) / 1000) + "s"
      : "-";
    return (
      <tr className="border-t border-indigo-100 text-sm">
        <td className="p-1 font-semibold">{job.status === "done" ? "Needs Review" : job.status}</td>
        <td className="p-1">{job.start_time ? new Date(job.start_time).toLocaleString() : "-"}</td>
        <td className="p-1">{job.end_time ? new Date(job.end_time).toLocaleString() : "-"}</td>
        <td className="p-1">{duration}</td>
        <td className="p-1 space-x-2">
          {job.status === "done" && (
            <>
              <Link className="text-indigo-700 underline" href={`review/${job.job_id}?agent=${agentId}`}>Review</Link>
              <button
                className="text-red-600 underline"
                onClick={async () => {
                  await updateNovelistJob(job.job_id, { action_needed: "done" }, token || "");
                  mutate();
                }}
              >
                {t("close_job")}
              </button>
            </>
          )}
        </td>
      </tr>
    );
  }

  const all = [...running, ...waiting, ...recent];

  return (
    <div className="overflow-x-auto border border-indigo-200 rounded-xl bg-white/90 shadow">
      <table className="min-w-full text-indigo-800">
        <thead>
          <tr className="text-left">
            <th className="p-1">Status</th>
            <th className="p-1">Started</th>
            <th className="p-1">Ended</th>
            <th className="p-1">Duration</th>
            <th className="p-1"></th>
          </tr>
        </thead>
        <tbody>
          {all.map(j => <Row key={j.job_id} job={j} />)}
        </tbody>
      </table>
    </div>
  );
}

const stepTitles = [
  "Source Text",
  "Instructions",
  "Previous Session",
  "Helper Agents",
  "Summary",
];

function AgentTip({ agent, children }: { agent: any; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 items-center bg-indigo-50 border border-indigo-200 rounded-xl px-4 py-2 mb-3">
      <Image src={agent?.logo || "/images/default/avatars/logo.png"} alt={agent?.name} width={32} height={32} className="w-8 h-8 rounded-full object-cover border" />
      <div className="text-sm text-indigo-700">{children}</div>
    </div>
  );
}

function StepIndicator({ step, total }: { step: number; total: number }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      {Array.from({ length: total }).map((_, i) => (
        <div
          key={i}
          className={`w-8 h-2 rounded-full transition-all ${
            i < step ? "bg-indigo-400" : "bg-indigo-100"
          }`}
        />
      ))}
      <span className="ml-2 text-indigo-600 text-xs">
        Step {step} of {total}
      </span>
    </div>
  );
}

function Modal({ open, onClose, children }: { open: boolean; onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    if (open) {
      document.body.style.overflow = "hidden";
      document.addEventListener("mousedown", handleClick);
    } else {
      document.body.style.overflow = "";
      document.removeEventListener("mousedown", handleClick);
    }
    return () => {
      document.body.style.overflow = "";
      document.removeEventListener("mousedown", handleClick);
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <motion.div
        ref={ref}
        className="relative w-full max-w-2xl bg-white border border-indigo-200 rounded-2xl shadow-xl p-6"
        initial={{ scale: 0.92, opacity: 0, y: 40 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.92, opacity: 0, y: 40 }}
      >
        <button className="absolute top-2 right-2 text-indigo-400 hover:text-indigo-700" onClick={onClose}>
          <X className="w-6 h-6" />
        </button>
        {children}
      </motion.div>
    </div>
  );
}

function CreateStoryModal({
  agent,
  agents,
  pages,
  onClose,
  onComplete,
}: {
  agent: any;
  agents: any[];
  pages: any[];
  onClose: () => void;
  onComplete: (params: {
    text: string;
    instructions: string;
    previous: number | "";
    helpers: number[];
  }) => void;
}) {
  const [step, setStep] = useState(1);
  const totalSteps = 5;

  // Step 1: source text
  const [inputMode, setInputMode] = useState<"upload" | "manual">("manual");
  const [text, setText] = useState("");
  const [uploadName, setUploadName] = useState<string | null>(null);

  // Step 2: instructions
  const [instructions, setInstructions] = useState("");

  // Step 3: previous session page
  const [previousPage, setPreviousPage] = useState<number | "">("");
  const [previousSearch, setPreviousSearch] = useState("");
  const filteredPages = useMemo(
    () =>
      !previousSearch
        ? pages
        : pages.filter((p) =>
            p.name.toLowerCase().includes(previousSearch.toLowerCase())
          ),
    [pages, previousSearch]
  );

  // Step 4: helpers
  const [helpers, setHelpers] = useState<number[]>(() =>
    agents.filter((a) => a.world_id === agent.world_id && a.id !== agent.id && a.task === "conversational").map(a => a.id)
  );

  // Step 5: summary

  // Go to next/prev
  function nextStep() {
    setStep((s) => Math.min(s + 1, totalSteps));
  }
  function prevStep() {
    setStep((s) => Math.max(s - 1, 1));
  }

  // Validate step (could add more logic)
  const canNext =
    (step === 1 && !!text.trim()) ||
    (step === 2 && !!instructions.trim()) ||
    step > 2;

  function handleComplete() {
    onComplete({ text, instructions, previous: previousPage, helpers });
    onClose();
  }

  // Helper agent selection candidates
  const helperCandidates = agents.filter(
    (a) =>
      a.world_id === agent.world_id &&
      a.id !== agent.id &&
      a.task === "conversational"
  );

  return (
    <div>
      <StepIndicator step={step} total={totalSteps} />
      <h2 className="text-xl font-bold text-indigo-700 mb-3">
        {stepTitles[step - 1]}
      </h2>
      {step === 1 && (
        <>
          <AgentTip agent={agent}>
            What’s the source of your story? Upload a file or write/paste your base text.
          </AgentTip>
          <div className="flex gap-2 mb-2">
            <button
              className={`px-3 py-1 rounded-xl border ${
                inputMode === "manual"
                  ? "bg-indigo-600 text-white"
                  : "bg-white text-indigo-700"
              } text-sm`}
              onClick={() => setInputMode("manual")}
            >
              Write/Paste
            </button>
            <button
              className={`px-3 py-1 rounded-xl border ${
                inputMode === "upload"
                  ? "bg-indigo-600 text-white"
                  : "bg-white text-indigo-700"
              } text-sm`}
              onClick={() => setInputMode("upload")}
            >
              Upload File
            </button>
          </div>
          {inputMode === "manual" ? (
            <RichEditor value={text} onChange={setText} onSave={() => {}} onCancel={undefined} showSaveButtons={false} />
          ) : (
            <div className="flex flex-col gap-2">
              <input
                type="file"
                accept=".txt,.md,.markdown,.pdf"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  setUploadName(file.name);
                  const reader = new FileReader();
                  reader.onload = (ev) => setText(ev.target?.result as string);
                  reader.readAsText(file);
                }}
                className="border border-indigo-200 rounded p-2"
              />
              {uploadName && (
                <div className="text-sm text-indigo-700">
                  Loaded {uploadName} ({text.split(/\s+/).filter(Boolean).length} words)
                </div>
              )}
              {text && (
                <pre className="whitespace-pre-wrap max-h-40 overflow-auto p-2 border border-indigo-200 rounded bg-white">
                  {text.slice(0, 500)}
                  {text.length > 500 ? "..." : ""}
                </pre>
              )}
            </div>
          )}
        </>
      )}

      {step === 2 && (
        <>
          <AgentTip agent={agent}>
            What instructions should I follow to novelize this text? Tell me about style, themes, or special requests.
          </AgentTip>
          <textarea
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="Instructions for the novelist"
            className="w-full h-24 p-2 border border-indigo-200 rounded"
          />
          <div className="flex gap-2 mt-1 text-xs text-indigo-400">
            <span>Examples:</span>
            <button onClick={() => setInstructions("Make it an epic fantasy adventure!")}>
              Epic fantasy
            </button>
            <button onClick={() => setInstructions("Write as a dark detective story.")}>
              Detective noir
            </button>
            <button onClick={() => setInstructions("Make it funny and whimsical.")}>
              Whimsical
            </button>
          </div>
        </>
      )}

      {step === 3 && (
        <>
          <AgentTip agent={agent}>
            Optionally pick the page from your previous session to help keep the story consistent.
          </AgentTip>
          <div className="mb-2 flex items-center gap-2">
            <Search className="w-4 h-4 text-indigo-500" />
            <input
              type="text"
              className="border rounded px-2 py-1 text-sm w-full"
              placeholder="Search pages by name..."
              value={previousSearch}
              onChange={(e) => setPreviousSearch(e.target.value)}
            />
          </div>
          <div className="max-h-40 overflow-auto mb-2 border border-indigo-100 rounded">
            <ul>
              {filteredPages.length === 0 && (
                <li className="px-3 py-2 text-indigo-400 italic">
                  No pages found
                </li>
              )}
              {filteredPages.slice(0, 15).map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => setPreviousPage(p.id)}
                    className={`flex items-center gap-2 px-3 py-2 w-full text-left ${
                      previousPage === p.id
                        ? "bg-indigo-100 font-bold"
                        : "hover:bg-indigo-50"
                    }`}
                  >
                    <BookOpen className="w-4 h-4 text-indigo-500" />
                    <span>{p.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="text-xs text-indigo-600">
            {previousPage
              ? <>Selected: <span className="font-semibold">{pages.find(p => p.id === previousPage)?.name}</span></>
              : "No previous session selected."}
          </div>
        </>
      )}

      {step === 4 && (
        <>
          <AgentTip agent={agent}>
            Would you like to summon any conversational helper agents to give richer world context?
          </AgentTip>
          <div className="grid grid-cols-2 gap-2 mt-1">
            {helperCandidates.length === 0 && (
              <div className="col-span-2 text-indigo-400">No conversational agents available in this world.</div>
            )}
            {helperCandidates.map((a) => (
              <label
                key={a.id}
                className={`flex items-center gap-2 border rounded-xl p-2 cursor-pointer ${
                  helpers.includes(a.id)
                    ? "bg-indigo-100 border-indigo-300"
                    : "bg-white border-indigo-200"
                }`}
              >
                <input
                  type="checkbox"
                  className="accent-indigo-600"
                  checked={helpers.includes(a.id)}
                  onChange={(e) => {
                    if (e.target.checked) setHelpers((h) => [...h, a.id]);
                    else setHelpers((h) => h.filter((id) => id !== a.id));
                  }}
                />
                {a.logo ? (
                  <Image
                    src={a.logo}
                    alt={a.name}
                    width={24}
                    height={24}
                    className="w-6 h-6 rounded-full object-cover"
                  />
                ) : (
                  <span className="w-6 h-6 rounded-full bg-indigo-200 flex items-center justify-center text-indigo-800 text-xs">
                    {a.name.slice(0, 1)}
                  </span>
                )}
                <span className="text-sm text-indigo-800">{a.name}</span>
              </label>
            ))}
          </div>
        </>
      )}

      {step === 5 && (
        <>
          <AgentTip agent={agent}>
            Here’s a summary. Ready to spin this tale?
          </AgentTip>
          <div className="bg-indigo-50 border border-indigo-200 rounded-xl p-3 text-sm text-indigo-800 space-y-2 mb-2">
            <div>
              <strong>Source:</strong>{" "}
              {text
                ? `${text.split(/\s+/).length} words`
                : "None"}
            </div>
            <div>
              <strong>Instructions:</strong>{" "}
              {instructions
                ? instructions.slice(0, 120) + (instructions.length > 120 ? "..." : "")
                : "None"}
            </div>
            <div>
              <strong>Previous Session:</strong>{" "}
              {previousPage
                ? pages.find((p) => p.id === previousPage)?.name
                : "None"}
            </div>
            <div>
              <strong>Helpers:</strong>{" "}
              {helperCandidates
                .filter((a) => helpers.includes(a.id))
                .map((a) => a.name)
                .join(", ") || "None"}
            </div>
          </div>
        </>
      )}

      {/* Controls */}
      <div className="flex gap-4 mt-4">
        {step > 1 && (
          <button
            className="px-4 py-2 rounded-xl bg-indigo-100 text-indigo-700 font-semibold border border-indigo-200"
            onClick={prevStep}
          >
            Back
          </button>
        )}
        {step < totalSteps && (
          <button
            className="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold"
            onClick={nextStep}
            disabled={!canNext}
          >
            Next
          </button>
        )}
        {step === totalSteps && (
          <button
            className="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold"
            onClick={handleComplete}
            disabled={!text || !instructions}
          >
            Let’s begin the tale!
          </button>
        )}
      </div>
    </div>
  );
}

// --- Main Page Component
function CreateNovelPageContent() {
  const { user, token } = useAuth();
  const searchParams = useSearchParams();
  const router = useRouter();
  const agentId = Number(searchParams.get("agent") || 0);
  const { agent } = useAgentById(agentId);
  const { agents } = useAgents();
  const { jobs, mutate } = useNovelistJobs();
  const { pages } = usePages(agent ? { gameworld_id: agent.world_id } : {});

  const [modalOpen, setModalOpen] = useState(false);

  async function handleCreate({
    text,
    instructions,
    previous,
    helpers,
  }: {
    text: string;
    instructions: string;
    previous: number | "";
    helpers: number[];
  }) {
    const res = await startNovelJob(
      agentId,
      {
        text,
        instructions,
        previous_page_id: previous || null,
        helper_agents: helpers,
      },
      token || ""
    );
    router.push(`/ai_novelist/create_novel?agent= ${agentId}`);
  }

  const allowed = useRoleRedirect("writer");
  if (!allowed) return null;

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full text-indigo-900 px-2 sm:px-6 py-10">
          <div className="mx-auto max-w-3xl flex flex-col gap-6">
            <h1 className="text-3xl font-bold text-indigo-700 mb-2">
              AI Novelist
            </h1>
            {agent && (
              <div className="flex items-center gap-4 bg-gradient-to-br from-indigo-100 via-purple-50 to-white border border-indigo-200 rounded-2xl p-4 shadow">
                <Image
                  src={agent.logo || "/images/default/avatars/logo.png"}
                  alt={agent.name}
                  width={64}
                  height={64}
                  className="w-16 h-16 rounded-full object-cover border-2 border-indigo-300"
                />
                <div>
                  <h2 className="text-2xl font-bold text-indigo-800 mb-1">
                    {agent.name}
                  </h2>
                  <div className="italic text-indigo-600 mb-1">
                    {AGENT_PERSONALITIES[agent.name] ||
                      AGENT_PERSONALITIES.default}
                  </div>
                  <div className="text-sm text-indigo-700">
                    Ready to help you craft new legends!
                  </div>
                </div>
              </div>
            )}

            <NovelistJobStatus agentId={agentId} jobs={jobs} />

            {/* Main Button to Create */}
            <button
              onClick={() => setModalOpen(true)}
              className="px-6 py-3 rounded-xl bg-indigo-600 text-white font-bold shadow-lg hover:bg-indigo-700 transition w-full mt-6 text-lg"
            >
              ✨ Start New Story
            </button>

            {/* Modal/Stepper */}
            <AnimatePresence>
              {modalOpen && agent && (
                <Modal open={modalOpen} onClose={() => setModalOpen(false)}>
                  <CreateStoryModal
                    agent={agent}
                    agents={agents}
                    pages={pages}
                    onClose={() => setModalOpen(false)}
                    onComplete={handleCreate}
                  />
                </Modal>
              )}
            </AnimatePresence>
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}

export default function CreateNovelPage() {
  return (
    <Suspense>
      <CreateNovelPageContent />
    </Suspense>
  );
}
