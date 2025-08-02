import { API_URL } from "./config";

export async function getSessions(tableId: number, token: string) {
  const res = await fetch(`${API_URL}/tables/${tableId}/sessions`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Failed to fetch sessions");
  return await res.json();
}

export async function createSession(tableId: number, data: unknown, token: string) {
  const res = await fetch(`${API_URL}/tables/${tableId}/sessions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}
