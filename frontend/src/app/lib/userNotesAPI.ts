import { API_URL } from "./config";

export async function getUserNotes(token: string) {
  const res = await fetch(`${API_URL}/user_notes/`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function getUserNote(id: number, token: string) {
  const res = await fetch(`${API_URL}/user_notes/${id}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function createUserNote(data: any, token: string) {
  const res = await fetch(`${API_URL}/user_notes/`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function updateUserNote(id: number, data: any, token: string) {
  const res = await fetch(`${API_URL}/user_notes/${id}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function deleteUserNote(id: number, token: string) {
  const res = await fetch(`${API_URL}/user_notes/${id}`, {
    method: "DELETE",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}
