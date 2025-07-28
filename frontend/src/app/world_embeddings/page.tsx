"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import { useWorlds } from "../lib/userWorlds";
import { useWorldEmbeddings } from "../lib/useWorldEmbeddings";
import { createWorldEmbedding, deleteWorldEmbedding, startWorldEmbeddingJob } from "../lib/worldEmbeddingAPI";
import { useWorldEmbeddingJobs } from "../lib/useWorldEmbeddingJobs";
import { useState } from "react";
import { Sparkles, BookOpen, Trash2, Globe, Pencil } from "lucide-react";
import WorldEmbeddingModal from "../components/world_embeddings/WorldEmbeddingModal";

export default function WorldEmbeddingsPage() {
  const { token } = useAuth();
  const { worlds } = useWorlds();
  const { embeddings, mutate } = useWorldEmbeddings();
  const { jobs, mutate: refreshJobs } = useWorldEmbeddingJobs();
  const [worldId, setWorldId] = useState("");
  const [name, setName] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [editing, setEditing] = useState<any | null>(null);
  const allowed = useRoleRedirect("system admin");
  if (!allowed) return null;

  async function handleCreate(e: any) {
    e.preventDefault();
    if (!worldId || !name) return;
    await createWorldEmbedding(
      {
        world_id: Number(worldId),
        name,
        collection: `world_${worldId}_${name}`,
      },
      token || ""
    );
    setName("");
    mutate();
    refreshJobs();
  }

  async function handleDelete(id: number) {
    if (!confirm("Are you sure you want to shatter this World Crystal?")) return;
    setDeletingId(id);
    await deleteWorldEmbedding(id, token || "");
    setDeletingId(null);
    mutate();
  }

  function openEdit(e: any) {
    setEditing(e);
  }

  function closeEdit() {
    setEditing(null);
    mutate();
  }

  async function handleRebuild(id: number) {
    await startWorldEmbeddingJob(id, token || "");
    refreshJobs();
  }

  function getWorldName(id: number) {
    return worlds.find((w: any) => w.id === id)?.name || "Unknown Realm";
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full px-2 sm:px-4 py-8 md:py-12 flex flex-col items-center bg-gradient-to-b from-slate-100 to-purple-50 dark:from-[#19112b] dark:to-[#3d214f] transition-colors">
          {/* Header */}
          <div className="max-w-2xl w-full mb-8 text-center">
            <div className="flex justify-center mb-2">
              <Sparkles className="text-purple-500 w-10 h-10 animate-pulse" />
            </div>
            <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-purple-900 dark:text-purple-200 drop-shadow-lg">
              World Embeddings Codex
            </h1>
            <p className="text-md md:text-lg text-slate-700 dark:text-slate-200 mt-2 italic">
              As Loremaster, you oversee the World Crystals. Harness their power to reveal the secrets of each realm.
            </p>
          </div>

          {/* "Ritual" Create Form */}
          <form
            onSubmit={handleCreate}
            className="w-full max-w-xl flex flex-col md:flex-row items-stretch gap-2 mb-10 bg-white/80 dark:bg-purple-900/50 rounded-2xl shadow-lg p-4 border-2 border-purple-200 dark:border-purple-700"
          >
            <select
              value={worldId}
              onChange={e => setWorldId(e.target.value)}
              className="flex-1 border-2 border-purple-300 dark:border-purple-700 rounded-xl px-3 py-2 bg-white/90 dark:bg-purple-950 text-lg focus:ring-2 focus:ring-purple-400"
              required
            >
              <option value="">Choose a Realm...</option>
              {worlds.map((w: any) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Crystal Name"
              className="flex-1 border-2 border-purple-300 dark:border-purple-700 rounded-xl px-3 py-2 bg-white/90 dark:bg-purple-950 text-lg focus:ring-2 focus:ring-purple-400"
              required
              maxLength={32}
            />
            <button
              type="submit"
              className="bg-gradient-to-br from-purple-600 to-indigo-500 hover:from-purple-700 hover:to-indigo-700 text-white font-bold px-6 py-2 rounded-xl shadow-md transition-all focus:ring-2 focus:ring-purple-400 flex items-center gap-2"
            >
              <Sparkles className="w-5 h-5" />
              Forge Crystal
            </button>
          </form>

          <table className="w-full border text-sm">
            <thead><tr><th>ID</th><th>Name</th><th>World</th><th>Pages</th><th>Last Build</th><th>Time</th><th></th></tr></thead>
            <tbody>
              {embeddings.map((e:any)=>(
                <tr key={e.id} className="border-t">
                  <td className="px-2">{e.id}</td>
                  <td className="px-2">{e.name}</td>
                  <td className="px-2">{getWorldName(e.world_id)}</td>
                  <td className="px-2">{e.page_count ?? 'None'}</td>
                  <td className="px-2">{e.last_index_time ? new Date(e.last_index_time).toLocaleString() : 'Never'}</td>
                  <td className="px-2">{e.build_seconds ? `${e.build_seconds}s` : '-'}</td>
                  <td className="px-2 flex gap-2">
                    <button onClick={()=>openEdit(e)} className="text-blue-600">Edit</button>
                    <button onClick={()=>handleRebuild(e.id)} className="text-purple-600">Rebuild</button>
                    <button onClick={()=>handleDelete(e.id)} className="text-red-600">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {jobs.length > 0 && (
            <div className="mt-8">
              <h2 className="text-xl font-semibold mb-2">Embedding Jobs</h2>
              <table className="w-full border text-sm">
                <thead><tr><th>ID</th><th>Status</th><th>Embedding</th></tr></thead>
                <tbody>
                  {jobs.map((j:any)=>(
                    <tr key={j.job_id} className="border-t">
                      <td className="px-2 font-mono">{j.job_id}</td>
                      <td className="px-2">{j.status}</td>
                      <td className="px-2">{j.embedding_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}


          {/* Embeddings Grid */}
          <div className="w-full max-w-5xl grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {embeddings.map((e: any) => (
              <div
                key={e.id}
                className="group relative bg-gradient-to-tr from-white via-purple-50 to-purple-200 dark:from-purple-950 dark:via-purple-900 dark:to-purple-800 border-2 border-purple-200 dark:border-purple-700 rounded-2xl p-5 shadow-xl flex flex-col gap-2 transition-transform hover:-translate-y-1 hover:shadow-2xl"
              >
                <div className="absolute top-3 right-3 flex gap-2">
                  <button
                    className="rounded-full bg-blue-100 hover:bg-blue-300 dark:bg-blue-900 dark:hover:bg-blue-700 p-2 shadow text-blue-600 dark:text-blue-300 transition"
                    title="Edit"
                    onClick={() => openEdit(e)}
                  >
                    <Pencil className="w-5 h-5" />
                  </button>
                  <button
                    className="rounded-full bg-red-100 hover:bg-red-300 dark:bg-red-900 dark:hover:bg-red-700 p-2 shadow text-red-600 dark:text-red-300 transition"
                    title="Shatter Crystal"
                    disabled={deletingId === e.id}
                    onClick={() => handleDelete(e.id)}
                  >
                    <Trash2 className="w-5 h-5" />
                  </button>
                </div>
                <div className="flex items-center gap-3 mb-2">
                  <BookOpen className="w-7 h-7 text-indigo-500 dark:text-indigo-300" />
                  <span className="text-lg font-semibold text-purple-900 dark:text-purple-200">
                    {e.name}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                  <Globe className="w-5 h-5 text-purple-400" />
                  <span>
                    <span className="font-semibold">{getWorldName(e.world_id)}</span> (ID: {e.world_id})
                  </span>
                </div>
                <div className="text-xs text-purple-800/70 dark:text-purple-100/70 mt-2">
                  Codex Entry ID: <span className="font-mono">{e.id}</span>
                </div>
                <div className="text-xs mt-1">Pages: {e.page_count ?? 'None'}</div>
                <div className="text-xs mt-1">Last Build: {e.last_index_time ? new Date(e.last_index_time).toLocaleString() : 'Never'}</div>
                <div className="text-xs mt-1">Build Time: {e.build_seconds ? `${e.build_seconds}s` : '-'}</div>
                <button
                  className="mt-2 px-3 py-1 rounded-xl bg-purple-600 text-white text-sm hover:bg-purple-700"
                  onClick={() => handleRebuild(e.id)}
                >
                  Rebuild
                </button>
              </div>
            ))}
            {embeddings.length === 0 && (
              <div className="col-span-full text-center p-10 text-lg text-slate-500 italic">
                No World Crystals yet.<br />Begin your ritual to forge the first!
              </div>
            )}
          </div>

        </div>
      </DashboardLayout>
      {editing && (
        <WorldEmbeddingModal
          embedding={editing}
          worlds={worlds}
          onClose={closeEdit}
          onSaved={closeEdit}
        />
      )}
    </AuthGuard>
  );
}
