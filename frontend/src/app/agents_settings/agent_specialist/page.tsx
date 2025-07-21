"use client";
import { useState } from "react";
import { Bot, Sparkles, Search, Database, CheckCircle2, XCircle } from "lucide-react";
import AuthGuard from "../../components/auth/AuthGuard";
import DashboardLayout from "../../components/DashboardLayout";
import { useAuth } from "../../components/auth/AuthProvider";
import { hasRole } from "../../lib/roles";
import { useAgents } from "../../lib/useAgents";
import { useWorlds } from "../../lib/userWorlds";
import { useAgentLibraryItems } from "../../lib/useAgentLibraryItems";
import AgentModal from "../../components/agents/AgentModal";
import AgentItemModal from "../../components/agents/AgentItemModal";
import { FileText, FileImage, FileArchive, FileVideo, FileAudio, File } from "lucide-react";


function getFileIcon(filename) {
  const ext = filename.split('.').pop()?.toLowerCase();
  if (["pdf"].includes(ext)) return <FileText className="text-rose-500 w-6 h-6" />;
  if (["png", "jpg", "jpeg", "gif", "bmp", "svg", "webp"].includes(ext)) return <FileImage className="text-green-500 w-6 h-6" />;
  if (["zip", "rar", "7z", "tar", "gz"].includes(ext)) return <FileArchive className="text-yellow-600 w-6 h-6" />;
  if (["mp4", "webm", "mov", "avi"].includes(ext)) return <FileVideo className="text-indigo-500 w-6 h-6" />;
  if (["mp3", "wav", "ogg", "flac"].includes(ext)) return <FileAudio className="text-blue-500 w-6 h-6" />;
  if (["txt", "md", "csv", "json"].includes(ext)) return <FileText className="text-gray-600 w-6 h-6" />;
  return <File className="text-indigo-400 w-6 h-6" />;
}

function getSourceDisplay(source) {  
  if (source.type === "link") {
    return (
      <a
        href={source.url}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-[var(--primary)] underline break-all hover:text-[var(--accent)]"
      >
        <LinkIcon className="w-4 h-4" />
        {source.url}
      </a>
    );
  }

  
  // const isLink = source.type === "link";


  // Assume "file"
  // Construct public URL (adjust base if needed)
  const fileUrl = source.path.startsWith("http")
    ? source.path
    : `https://shrecknet.club/uploads/${source.path.replace(/^\/+/, "")}`;
  return (
    <a
      href={fileUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-[var(--primary)] underline break-all hover:text-[var(--accent)]"
    >
      {getFileIcon(source.path)}
      {source.name || source.path.split("/").pop()}
    </a>
  );
}

// ----- NPC Guide -----
const npcQuotes = [
  "Welcome, archivist! Your specialists await new knowledge.",
  "A tidy archive is a powerful tool.",
  "Feed your specialists well, and they will answer wisely.",
];
function GuildmasterGuide({ status, quote, flavor }) {
  return (
    <div className="flex gap-4 items-center rounded-xl p-4 mb-4 bg-gradient-to-r from-indigo-50 via-purple-50 to-white shadow border border-indigo-100">
      <div className="flex flex-col items-center">
        <div className="w-14 h-14 bg-indigo-200 rounded-full flex items-center justify-center shadow-inner text-indigo-700 text-3xl border-4 border-indigo-300">
          🧙‍♂️
        </div>
        <span className="text-xs text-indigo-600 mt-1 font-mono">Archivist</span>
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

// ----- Avatar -----
function AgentAvatar({ name, logo }) {
  const initials = name
    .split(" ")
    .map(n => n[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center text-lg font-bold text-indigo-700 shadow-inner border-2 border-indigo-200 overflow-hidden">
      {logo ? (
        <img src={logo} alt={name} className="object-cover w-full h-full" onError={e => { e.currentTarget.style.display = "none"; e.currentTarget.parentNode.textContent = initials; }} />
      ) : (
        initials
      )}
    </div>
  );
}

// ----- Job Status -----

// ----- Item Cards -----
function ItemCard({ item, onRemove, selected, onSelect }) {
  return (
    <div className="border border-[var(--primary)] bg-[var(--card)] rounded-2xl p-4 shadow-lg flex flex-col gap-1">
      <div className="flex items-center gap-2 mb-1">
        <div className="bg-[var(--accent)] rounded-full p-2 flex items-center justify-center shadow-inner">
          {getFileIcon(item.path)}
        </div>
        <div className="font-semibold text-[var(--primary)] truncate flex-1">{item.name}</div>
        <input type="checkbox" className="accent-[var(--primary)]" checked={selected} onChange={onSelect} />
      </div>
      {/* <div className="text-sm break-all mb-1">
        {item.path}
      </div> */}
      <div className="text-xs text-[var(--muted-foreground)] flex items-center gap-1">
        <span>Added: {item.added_at ? new Date(item.added_at).toLocaleString() : "—"}</span>
      </div>
      <div className="flex items-center justify-between mt-1">
        <div className="text-xs flex items-center gap-1">
          <Database className="w-4 h-4" />
          {item.vector_db_update_date ? (
            <span className="flex items-center gap-1 text-green-700"><CheckCircle2 className="w-4 h-4" /> {new Date(item.vector_db_update_date).toLocaleDateString()}</span>
          ) : (
            <span className="flex items-center gap-1 text-red-700"><XCircle className="w-4 h-4" /> none</span>
          )}
        </div>
        <button
          className="px-2 py-1 text-xs rounded-xl font-bold bg-red-600 text-white hover:bg-red-700 shadow transition"
          onClick={() => onRemove(item)}
        >
          Remove
        </button>
      </div>
    </div>
  );
}

// ----- Specialist Agent Card -----
function SpecialistAgentCard({
  agent,
  worlds,
  onEdit,
  setItemModal,
}) {
  const { items, mutate: refreshItems } = useAgentLibraryItems(agent.id);
  const { token } = useAuth();
  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  function toggleSelect(id:number) {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]);
  }

  async function removeSelected() {
    if (selectedIds.length === 0) return;
    if (!confirm("Remove selected items from agent?")) return;
    const { unlinkAgentItem } = await import("../../lib/specialistAPI");
    for (const id of selectedIds) {
      await unlinkAgentItem(agent.id, id, token || "");
    }
    setSelectedIds([]);
    refreshItems();
  }

  async function removeItem(item) {
    if (!confirm("Remove item from agent?")) return;
    const { unlinkAgentItem } = await import("../../lib/specialistAPI");
    await unlinkAgentItem(agent.id, item.id, token || "");
    refreshItems();
  }
  return (
    <div className="border border-indigo-100 bg-white/80 rounded-2xl shadow p-4">
      <div className="flex items-center gap-4">
        <AgentAvatar name={agent.name} logo={agent.logo} />
        <div className="flex-1">
          <div className="font-bold text-indigo-800">{agent.name}</div>
          <div className="text-sm text-indigo-700 opacity-70">
            World: <span className="font-semibold">{worlds?.find(w => w.id === agent.world_id)?.name || "???"}</span>
          </div>
          {agent.specialist_update_date && (
            <div className="text-sm text-indigo-700 opacity-70">
              Vector DB updated: <span className="font-semibold">{new Date(agent.specialist_update_date).toLocaleString()}</span>
            </div>
          )}
        </div>
        <div className="flex gap-2 flex-wrap">
          <button className="px-3 py-1 rounded-lg font-semibold text-white bg-indigo-600 hover:bg-indigo-800 transition text-sm shadow" onClick={onEdit}>Edit</button>
          <button className="px-3 py-1 rounded-lg font-semibold text-white bg-sky-600 hover:bg-sky-800 transition text-sm shadow" onClick={() => setItemModal({agentId: agent.id})}>Add Library</button>
        </div>
      </div>
      {items && items.length > 0 && (
        <>
          <div className="mt-3 grid sm:grid-cols-2 md:grid-cols-3 gap-3">
            {items.map(it => (
              <ItemCard
                key={it.id}
                item={it}
                onRemove={(i) => removeItem(i)}
                selected={selectedIds.includes(it.id)}
                onSelect={() => toggleSelect(it.id)}
              />
            ))}
          </div>
          {selectedIds.length > 0 && (
            <div className="mt-3">
              <button
                className="px-4 py-2 rounded-xl bg-red-600 text-white hover:bg-red-700 shadow"
                onClick={removeSelected}
              >
                Remove Selected
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ----- Main Page -----
export default function SpecialistSettingsPage() {
  const { user, token } = useAuth();
  const { agents, mutate } = useAgents();
  const { worlds } = useWorlds();
  const [modalOpen, setModalOpen] = useState(false);
  const [itemModal, setItemModal] = useState<{agentId:number}|null>(null);
  const [selectedAgent, setSelectedAgent] = useState(null as any);
  const [success, setSuccess] = useState("");
  const [npcFlavor, setNpcFlavor] = useState("Manage your specialist agents and their library items here.");
  const [npcQuote] = useState(npcQuotes[Math.floor(Math.random()*npcQuotes.length)]);

  if (!hasRole(user?.role, "world builder") && !hasRole(user?.role, "system admin")) {
    return (
      <DashboardLayout>
        <div className="p-10 text-2xl text-red-600 font-bold">Not authorized</div>
      </DashboardLayout>
    );
  }

  const specialists = agents.filter(a => a.task === "specialist");

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full text-indigo-900 px-2 sm:px-6 py-8">
          <div className="mx-auto max-w-5xl w-full">
            <GuildmasterGuide status={success} quote={npcQuote} flavor={npcFlavor} />

            <div className="flex flex-col sm:flex-row gap-4 items-center justify-between mb-5">
              <div className="flex gap-2">
                <button className="flex gap-2 items-center px-5 py-2 rounded-xl font-bold bg-indigo-600 text-white shadow hover:bg-indigo-800 border border-indigo-500 transition" onClick={()=>{setSelectedAgent(null);setModalOpen(true);}}>
                  <Sparkles className="w-5 h-5" /> Add Agent
                </button>
              </div>
              <div className="flex items-center gap-2 bg-white border border-indigo-200 px-4 py-2 rounded-xl shadow-inner w-full sm:w-[340px]">
                <Search className="w-5 h-5 text-indigo-400" />
                <input className="bg-transparent outline-none flex-1 text-base text-indigo-700 placeholder-indigo-400" placeholder="Search agents..." onChange={()=>{}} />
              </div>
            </div>

            <div className="space-y-6">
              {specialists.map(agent => (
                <SpecialistAgentCard
                  key={agent.id}
                  agent={agent}
                  worlds={worlds}
                  onEdit={() => { setSelectedAgent(agent); setModalOpen(true); }}
                  setItemModal={setItemModal}
                />
              ))}
            </div>

            {modalOpen && (
              <AgentModal
                agent={selectedAgent}
                onClose={()=>setModalOpen(false)}
                onSave={()=>{mutate();setModalOpen(false);}}
                onDelete={()=>{mutate();setModalOpen(false);}}
                worlds={worlds}
              />
            )}
            {itemModal && (
              <AgentItemModal
                agentId={itemModal.agentId}
                onClose={()=>setItemModal(null)}
                onSaved={()=>{}}
              />
            )}
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
