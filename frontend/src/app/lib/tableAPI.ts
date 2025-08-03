import { API_URL } from "./config";

export async function getTables(token: string) {
  const res = await fetch(`${API_URL}/tables/`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Failed to fetch tables");
  return await res.json();
}

export async function createTable(data: unknown, token: string) {
  const res = await fetch(`${API_URL}/tables/`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function updateTable(id: number, data: unknown, token: string) {
  const res = await fetch(`${API_URL}/tables/${id}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function deleteTable(id: number, token: string) {
  const res = await fetch(`${API_URL}/tables/${id}`, {
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}
