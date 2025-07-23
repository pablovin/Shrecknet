// app/lib/pagesAPI.ts
import { API_URL } from "./config";

// GET all pages (optionally filter by world/concept)
export async function getPages(token: string, params: { gameworld_id?: number; concept_id?: number } = {}) {
  const query = new URLSearchParams();
  if (params.gameworld_id) query.append("gameworld_id", params.gameworld_id.toString());
  if (params.concept_id) query.append("concept_id", params.concept_id.toString());
  const res = await fetch(`${API_URL}/pages/?${query.toString()}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Could not fetch pages");
  return await res.json();
}

// GET a specific page by ID (with all characteristic values)
export async function getPage(id: number, token: string) {
  const res = await fetch(`${API_URL}/pages/${id}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Page not found");
  return await res.json();
}

// CREATE a page (with characteristic values)
export async function createPage(data: unknown, token: string) {

  console.log("Payload: "+JSON.stringify(data))
  const res = await fetch(`${API_URL}/pages/`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

// UPDATE a page (and its characteristic values)
export async function updatePage(id: number, data: unknown, token: string) {

  console.log("id:"+id)
  console.log("Payload:"+JSON.stringify(data))

  const res = await fetch(`${API_URL}/pages/${id}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

// DELETE a page
export async function deletePage(id: number, token: string) {
  const res = await fetch(`${API_URL}/pages/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

// --- Optional: Get pages for a concept, or for a world, as helpers ---

export async function getPagesForConcept(concept_id: number, token: string) {
  return getPages(token, { concept_id });
}

export async function getPagesForWorld(gameworld_id: number, token: string) {
  return getPages(token, { gameworld_id });
}

// --- Key Events ---
export async function createKeyEvent(pageId: number, data: unknown, token: string) {
  const res = await fetch(`${API_URL}/pages/${pageId}/events/`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function updateKeyEvent(eventId: number, data: unknown, token: string) {
  const res = await fetch(`${API_URL}/pages/events/${eventId}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function deleteKeyEvent(eventId: number, token: string) {
  const res = await fetch(`${API_URL}/pages/events/${eventId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

// --- Relationships ---
export async function createRelationship(pageId: number, data: unknown, token: string) {
  const res = await fetch(`${API_URL}/pages/${pageId}/relationships/`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function updateRelationship(relId: number, data: unknown, token: string) {
  const res = await fetch(`${API_URL}/pages/relationships/${relId}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function deleteRelationship(relId: number, token: string) {
  const res = await fetch(`${API_URL}/pages/relationships/${relId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}
