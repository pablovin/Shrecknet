"use client";
import { useState } from "react";
import { Bot, Sparkles, Wand2, Search } from "lucide-react";
import AuthGuard from "../../components/auth/AuthGuard";
import DashboardLayout from "../../components/DashboardLayout";
import { useAuth } from "../../components/auth/AuthProvider";
import useRoleRedirect from "../../hooks/useRoleRedirect";
import { useAgents } from "../../lib/useAgents";
import { useWorlds } from "../../lib/userWorlds";
import AgentModal from "../../components/agents/AgentModal";
import AgentEmbeddingModal from "../../components/agents/AgentEmbeddingModal";
import { startVectorUpdate } from "../../lib/vectordbAPI";
import { getPagesForWorld } from "../../lib/pagesAPI";
import { useAgentEmbeddings } from "../../lib/useAgentEmbeddings";
import { useWriterJobs } from "../../lib/useWriterJobs";
import { usePageById } from "../../lib/usePageById";

// ------- GUILDMASTER NPC GUIDE -------
const npcQuotes = [
  "Welcome to the Guildhall, worldbuilder! Here your agents await their next quest.",
  "A wise admin listens to their guides—and their Guild.",
  "Remember: a well-maintained archive is a happy archive.",
  "Need a new conversationalist? Add one, and let the stories flow.",
  "Our scribes and novelists await your guidance.",
  "Bulk rituals are best performed at dawn. Or with coffee.",
];
function GuildmasterGuide({ status, quote, flavor }) {
  // Use an image for the Guildmaster avatar, or just an emoji!
  return (
    <div className="flex gap-4 items-center rounded-xl p-4 mb-4 bg-gradient-to-r from-indigo-50 via-purple-50 to-white shadow border border-indigo-100">
      {/* You can use a real image, or SVG, or just the Bot icon here */}
      <div className="flex flex-col items-center">
        <div className="w-14 h-14 bg-indigo-200 rounded-full flex items-center justify-center shadow-inner text-indigo-700 text-3xl border-4 border-indigo-300">
          {/* <img src="/images/guildmaster.png" className="w-12 h-12 rounded-full" alt="Guildmaster" /> */}
          🧙‍♂️
        </div>
        <span className="text-xs text-indigo-600 mt-1 font-mono">Guildmaster</span>
      </div>
      <div className="flex-1">
        <div className="text-indigo-900 font-semibold italic">{quote}</div>
        <div className="text-xs text-indigo-700 mt-1">{flavor}</div>
        {status && (
          <div className="text-sm bg-indigo-200 text-indigo-900 rounded px-3 py-1 mt-2 w-fit font-semibold shadow-sm">{status}</div>
        )}
      </div>
    </div>
  );
}

// ------- AGENT AVATAR -------
function AgentAvatar({ name, logo }) {
  // If the logo exists, show it. Otherwise, show initials as a fallback.
  const initials = name
    .split(" ")
    .map((n) => n[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center text-lg font-bold text-indigo-700 shadow-inner border-2 border-indigo-200 overflow-hidden">
      {logo ? (
        <img
          src={logo}
          alt={name}
          className="object-cover w-full h-full"
          onError={(e) => {
            // fallback to initials if image fails to load
            e.currentTarget.style.display = "none";
            e.currentTarget.parentNode.textContent = initials;
          }}
        />
      ) : (
        initials
      )}
    </div>
  );
}

// ------- Agent Embedding Info -------
function AgentEmbeddingInfo({ agentId }) {
  const { embeddings } = useAgentEmbeddings(agentId);
  if (!embeddings.length) return null;
  return (
    <div className="text-xs text-indigo-700 mt-1 space-y-1">
      {embeddings.map((e:any) => (
        <div key={e.id}>
          <span className="font-semibold">{e.name}</span> - {e.page_count ?? "?"} pages,
          {" "}last: {e.last_index_time ? new Date(e.last_index_time).toLocaleString() : "n/a"} ({e.build_seconds ? e.build_seconds.toFixed(1)+"s" : "?"})
        </div>
      ))}
    </div>
  );
}


// --------- WriterJobStatusScroll ---------
function WriterJobStatusScroll({ jobs }) {
  if (!jobs || !jobs.length) return null;

  // Active and finished jobs
  const activeJobs = jobs.filter(j =>
    j.status === "queued" || j.status === "running" || j.status === "processing"
  );
  const finishedJobs = jobs
    .filter(j => j.status === "finished" || j.status === "done" || j.status === "error")
    .slice(-5);

  function JobRow({ job }) {
    // Page name loader with fallback to ID
    const { page } = usePageById(job.page_id);

    const typeLabel = job.job_type?.replaceAll("_", " ");

    return (
      <li className="mb-1">
        {/* Status */}
        <span>
          {(job.status === "running" || job.status === "processing") && <><span className="animate-pulse">⏳</span> Running</>}
          {job.status === "queued" && <><span>🕓</span> Queued</>}
          {(job.status === "finished" || job.status === "done") && <><span>✅</span> Done</>}
          {job.status === "error" && <><span>❌</span> Error</>}
          {!["queued", "running", "finished", "done", "error"].includes(job.status) && <>🔄 {job.status}</>}
        </span>
        {" — "}
        <span className="font-semibold">{typeLabel}</span>
        {job.page_id && (
          <span> — Page: <span className="font-semibold">{page?.name || `#${job.page_id}`}</span></span>
        )}
        <div className="text-xs text-gray-500 ml-5">
          Start: {job.start_time ? new Date(job.start_time).toLocaleString() : "?"}
          {job.end_time && <> | End: {new Date(job.end_time).toLocaleString()}</>}
        </div>
        {(job.status === "done" || job.status === "finished") && Array.isArray(job.suggestions) && (
          <div className="text-xs text-sky-800 ml-5">
            Suggestions: <span className="font-semibold">{job.suggestions.length}</span>
          </div>
        )}
      </li>
    );
  }

  return (
    <div className="bg-cyan-50 border border-cyan-200 rounded-xl p-2 mt-2 shadow-inner">
      <div className="font-semibold text-cyan-900 mb-1 flex items-center gap-1">
        <Sparkles className="w-4 h-4 text-cyan-600" /> Writer Agent Jobs
      </div>
      {activeJobs.length > 0 && (
        <>
          <div className="font-semibold text-cyan-700">Active Jobs:</div>
          <ul className="pl-3 text-sm text-cyan-900 mb-2">
            {activeJobs.map((job, idx) => <JobRow key={job.id || idx} job={job} />)}
          </ul>
        </>
      )}
      {finishedJobs.length > 0 && (
        <>
          <div className="font-semibold text-cyan-700 mt-2">Recent Jobs:</div>
          <ul className="pl-3 text-sm text-cyan-900">
            {finishedJobs.map((job, idx) => <JobRow key={job.id || idx} job={job} />)}
          </ul>
        </>
      )}
    </div>
  );
}




// ------- MAIN PAGE -------
export default function AgentsGuildhallPage() {
  const pageInfo = {
    title: "Conversationalists’ Table",
    icon: <Bot className="w-6 h-6 text-indigo-500" />,
    task: "conversational",
    canRebuild: true,
  };

  const { user, token } = useAuth();
  const { agents, mutate, isLoading, error } = useAgents();
  const { worlds } = useWorlds();
  const { jobs: writerJobs } = useWriterJobs();

  const [modalOpen, setModalOpen] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState(null);
  const [embeddingModal, setEmbeddingModal] = useState<{id:number, world:number}|null>(null);
  const [success, setSuccess] = useState("");
  const [updatingAgentId, setUpdatingAgentId] = useState<number | null>(null);
  const [bulkUpdating, setBulkUpdating] = useState(false);
  const [bulkStatus, setBulkStatus] = useState<string[]>([]);
  const [npcFlavor, setNpcFlavor] = useState(
    "You can add, edit, or send your agents on new tasks from here."
  );
  const [npcQuote] = useState(
    npcQuotes[Math.floor(Math.random() * npcQuotes.length)]
  );

  const allowed = useRoleRedirect("world builder");
  if (!allowed) return null;
// Filter/search logic
const filtered = agents.filter((a) =>
  a.name?.toLowerCase().includes(search.toLowerCase())
);

// Group jobs by agent
  .filter((j) => j.job_type === "update_vector_db")
  .reduce<Record<number, any[]>>((acc, j) => {
    if (!acc[j.agent_id]) acc[j.agent_id] = [];
    acc[j.agent_id].push(j);
    return acc;
  }, {});

const writerJobsByAgent = writerJobs
  .filter(j => j.job_type === "analyze_pages")
  .reduce<Record<number, any[]>>((acc, j) => {
    if (!acc[j.agent_id]) acc[j.agent_id] = [];
    acc[j.agent_id].push(j);
    return acc;
  }, {});

// Bulk vector update
async function handleRebuildAll() {
  const conversational = agents.filter((a) => a.task === "conversational");
  if (conversational.length === 0) return;
  setBulkUpdating(true);
  setBulkStatus([]);
  setNpcFlavor("Guild ritual in progress: refreshing all conversational agents...");
  for (const ag of conversational) {
    setBulkStatus((s) => [...s, `Updating ${ag.name}...`]);
    try {
      await startVectorUpdate(token || "", ag.id);
      setBulkStatus((s) => [...s, `✅ ${ag.name}: job queued.`]);
    } catch (err) {
      setBulkStatus((s) => [...s, `❌ ${ag.name}: failed to queue.`]);
    }
  }
  setBulkUpdating(false);
}

// Individual vector update
async function handleRebuild(agent) {
  setUpdatingAgentId(agent.id);
  setNpcFlavor(`Agent ${agent.name} is updating their knowledge...`);
  try {
    const pages = await getPagesForWorld(agent.world_id, token || "");
    const pageCount = pages.length;
    const estimated = Math.ceil(pageCount * 1.5);
    const proceed = window.confirm(
      `Rebuilding the vector DB will add ${pageCount} pages and may take around ${estimated} seconds. Continue?`
    );
    if (!proceed) {
      setUpdatingAgentId(null);
      setNpcFlavor("Action cancelled.");
      return;
    }
    await startVectorUpdate(token || "", agent.id);
    setSuccess("Vector DB update started");
  } catch (err) {
    setSuccess("Failed to rebuild vector DB");
    setNpcFlavor("Something went wrong with the ritual...");
  } finally {
    setUpdatingAgentId(null);
    setTimeout(() => setSuccess(""), 2000);
  }
}

// Modal openers/closers
function handleCreate() {
  setSelectedAgent(null);
  setModalOpen(true);
  setNpcFlavor("Time to welcome a new agent into the Guild!");
}
function handleEdit(agent) {
  setSelectedAgent(agent);
  setModalOpen(true);
  setNpcFlavor(`Editing agent ${agent.name}...`);
}
function handleModalSave() {
  mutate();
  setModalOpen(false);
  setSuccess("Agent saved successfully!");
  setNpcFlavor("Success! The Guild welcomes the updated agent.");
  setTimeout(() => setSuccess(""), 2000);
}
function handleModalDelete() {
  mutate();
  setModalOpen(false);
  setSuccess("Agent deleted successfully!");
  setNpcFlavor("The Guild bid farewell to the departed agent.");
  setTimeout(() => setSuccess(""), 2000);
}

// -------- AGENT GROUP CARD RENDERER --------
function AgentGuildTable({ title, icon, agents, task, canRebuild }) {
  return (
    <div className="mb-10">
      <div className="flex items-center gap-2 mb-2">
        <span>{icon}</span>
        <h2 className="text-xl font-bold text-indigo-900 font-serif">{title}</h2>
      </div>
      <div className="rounded-2xl shadow bg-white/80 border border-indigo-100 p-2 sm:p-4">
        {agents.length === 0 && (
          <div className="text-indigo-400 italic text-sm p-3">No agents in this guild yet.</div>
        )}
        <div className="divide-y divide-indigo-50">
          {agents.map((agent) => (
            <div key={agent.id} className="flex flex-col gap-2 py-4">
              <div className="flex flex-row items-center gap-4">
                <AgentAvatar name={agent.name} logo={agent.logo} />
                <div className="flex-1">
                  <div className="font-bold text-indigo-800">{agent.name}</div>
                  <div className="text-sm text-indigo-700 opacity-70">
                    World: <span className="font-semibold">{worlds?.find(w => w.id === agent.world_id)?.name || "???"}</span>
                  </div>
                  {agent.vector_db_update_date && (
                    <div className="text-sm text-indigo-700 opacity-70">
                      Vector DB updated: <span className="font-semibold">{new Date(agent.vector_db_update_date).toLocaleString()}</span>
                    </div>
                  )}
                  <AgentEmbeddingInfo agentId={agent.id} />
                </div>
                <div className="flex gap-2">
                  <button
                    className="px-3 py-1 rounded-lg font-semibold text-white bg-indigo-600 hover:bg-indigo-800 transition text-sm shadow"
                    onClick={() => handleEdit(agent)}
                  >
                    Edit
                  </button>
                <button
                  className="px-3 py-1 rounded-lg font-semibold text-white bg-rose-600 hover:bg-rose-800 transition text-sm shadow"
                  onClick={() => {
                    setSelectedAgent(agent);
                    setModalOpen(true);
                    setNpcFlavor(`Are you sure you want to remove agent ${agent.name}?`);
                  }}
                >
                  Delete
                </button>
                <button
                  className="px-3 py-1 rounded-lg font-semibold text-white bg-sky-600 hover:bg-sky-800 transition text-sm shadow"
                  onClick={() => setEmbeddingModal({id:agent.id, world:agent.world_id})}
                >
                  Embeddings
                </button>
                {canRebuild && (
                    <button
                      className="px-3 py-1 rounded-lg font-semibold text-indigo-700 bg-purple-200 hover:bg-yellow-300 transition text-sm shadow border border-purple-300"
                      onClick={() => handleRebuild(agent)}
                      disabled={updatingAgentId === agent.id}
                    >
                      {updatingAgentId === agent.id ? "Updating..." : "Rebuild Vector"}
                    </button>
                  )}
                </div>
              </div>
              {task === "page writer" && writerJobsByAgent[agent.id] && (
                <WriterJobStatusScroll jobs={writerJobsByAgent[agent.id]} />
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

return (
  <AuthGuard>
    <DashboardLayout>
      <div className="min-h-screen w-full text-indigo-900 px-2 sm:px-6 py-8">
        <div className="mx-auto max-w-5xl w-full">
          {/* Guildmaster NPC Guide */}
          <GuildmasterGuide
            status={success}
            quote={npcQuote}
            flavor={npcFlavor}
          />

          {/* Guild Rituals and search */}
          <div className="flex flex-col sm:flex-row gap-4 items-center justify-between mb-5">
            <div className="flex gap-2">
              <button
                className="flex gap-2 items-center px-5 py-2 rounded-xl font-bold bg-indigo-600 text-white shadow hover:bg-indigo-800 border border-indigo-500 transition"
                onClick={handleCreate}
              >
                <Sparkles className="w-5 h-5" />
                Add Agent
              </button>
              {pageInfo.task === "conversational" && (
                <button
                  className={`flex gap-2 items-center px-5 py-2 rounded-xl font-bold bg-purple-200 text-indigo-800 shadow border border-purple-300 hover:bg-purple-300 transition ${
                    bulkUpdating ? "opacity-60 cursor-not-allowed" : ""
                  }`}
                  onClick={handleRebuildAll}
                  disabled={bulkUpdating}
                >
                  <Wand2 className="w-5 h-5" />
                  {bulkUpdating ? "Ritual in Progress..." : "Update All Conversational Vectors"}
                </button>
              )}
            </div>
            {/* Scrying/search bar */}
            <div className="flex items-center gap-2 bg-white border border-indigo-200 px-4 py-2 rounded-xl shadow-inner w-full sm:w-[340px]">
              <Search className="w-5 h-5 text-indigo-400" />
              <input
                className="bg-transparent outline-none flex-1 text-base text-indigo-700 placeholder-indigo-400"
                placeholder="Scry for NPCs..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          {/* Bulk status feedback */}
          {pageInfo.task === "conversational" && bulkStatus.length > 0 && (
            <div className="bg-indigo-50 border border-indigo-200 rounded-xl px-4 py-2 mb-4 text-sm space-y-1">
              {bulkStatus.map((msg, i) => (
                <div key={i}>{msg}</div>
              ))}
            </div>
          )}

          {/* Agent guild table for selected type */}
          <AgentGuildTable
            title={pageInfo.title}
            icon={pageInfo.icon}
            agents={filtered.filter((a) => a.task === pageInfo.task)}
            task={pageInfo.task}
            canRebuild={pageInfo.canRebuild}
          />

          {/* Modal for Add/Edit agent */}
          {modalOpen && (
            <AgentModal
              agent={selectedAgent}
              onClose={() => setModalOpen(false)}
              onSave={handleModalSave}
              onDelete={handleModalDelete}
              worlds={worlds}
            />
          )}
          {embeddingModal && (
            <AgentEmbeddingModal
              agentId={embeddingModal.id}
              worldId={embeddingModal.world}
              onClose={()=>setEmbeddingModal(null)}
              onSaved={()=>{}}
            />
          )}
        </div>
      </div>
    </DashboardLayout>
  </AuthGuard>
);
}
