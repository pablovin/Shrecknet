import { API_URL } from "./config";

export async function getWorldEmbeddings(token: string) {
  const res = await fetch(`${API_URL}/world_embeddings/`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function createWorldEmbedding(data: any, token: string) {
  const res = await fetch(`${API_URL}/world_embeddings/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function deleteWorldEmbedding(id: number, token: string) {
  const res = await fetch(`${API_URL}/world_embeddings/${id}`, {
    method: "DELETE",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function startWorldEmbeddingJob(id: number, token: string) {
  const res = await fetch(`${API_URL}/world_embeddings/${id}/embed_async`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function listWorldEmbeddingJobs(token: string) {
  const res = await fetch(`${API_URL}/world_embeddings/vector_jobs`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function getAgentEmbeddings(agentId: number, token: string) {
  const res = await fetch(`${API_URL}/world_embeddings/agents/${agentId}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function setAgentEmbeddings(agentId: number, ids: number[], token: string) {
  const res = await fetch(`${API_URL}/world_embeddings/agents/${agentId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ embedding_ids: ids }),
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}
