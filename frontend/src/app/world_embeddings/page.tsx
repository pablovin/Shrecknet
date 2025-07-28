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

export default function WorldEmbeddingsPage() {
  const { token } = useAuth();
  const { worlds } = useWorlds();
  const { embeddings, mutate } = useWorldEmbeddings();
  const { jobs, mutate: refreshJobs } = useWorldEmbeddingJobs();
  const [worldId, setWorldId] = useState("");
  const [name, setName] = useState("");
  const allowed = useRoleRedirect("system admin");
  if (!allowed) return null;

  async function handleCreate(e:any) {
    e.preventDefault();
    if (!worldId || !name) return;
    await createWorldEmbedding({ world_id: Number(worldId), name, collection: `world_${worldId}_${name}` }, token||"" );
    setName("");
    mutate();
    refreshJobs();
  }

  async function handleDelete(id:number) {
    if (!confirm("Delete embedding?")) return;
    await deleteWorldEmbedding(id, token||"");
    mutate();
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full px-4 py-10">
          <h1 className="text-2xl font-bold mb-4">World Embeddings</h1>
          <form onSubmit={handleCreate} className="flex gap-2 mb-4">
            <select value={worldId} onChange={e=>setWorldId(e.target.value)} className="border p-1 rounded">
              <option value="">Select world</option>
              {worlds.map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}
            </select>
            <input value={name} onChange={e=>setName(e.target.value)} placeholder="Name" className="border p-1 rounded" />
            <button type="submit" className="btn-primary">Create</button>
          </form>
          <table className="w-full border">
            <thead><tr><th>ID</th><th>Name</th><th>World</th><th></th></tr></thead>
            <tbody>
              {embeddings.map((e:any)=>(
                <tr key={e.id} className="border-t">
                  <td className="px-2">{e.id}</td>
                  <td className="px-2">{e.name}</td>
                  <td className="px-2">{e.world_id}</td>
                  <td className="px-2"><button onClick={()=>handleDelete(e.id)} className="text-red-600">Delete</button></td>
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
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
