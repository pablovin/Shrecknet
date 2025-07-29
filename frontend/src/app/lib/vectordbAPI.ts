import { API_URL } from "./config";

export async function rebuildVectorDB(token: string, embeddingId: number) {
  const res = await fetch(`${API_URL}/vectordb/${embeddingId}/rebuild`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    let err;
    try { err = await res.json(); }
    catch { err = await res.text(); }
    throw err;
  }
  return await res.json();
}

export async function startVectorUpdate(token: string, agentId: number) {
  const res = await fetch(`${API_URL}/agents/${agentId}/update_vector_db`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function listVectorJobs(token: string) {
  const res = await fetch(`${API_URL}/agents/vector_jobs`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}
