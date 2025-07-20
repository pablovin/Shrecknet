import { API_URL } from "./config";

export async function getLibraryItems(token: string) {
  const res = await fetch(`${API_URL}/library/`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function createLibraryItem(form: FormData, token: string) {
  const res = await fetch(`${API_URL}/library/`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function updateLibraryItem(id: number, data: any, token: string) {
  const res = await fetch(`${API_URL}/library/${id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function deleteLibraryItem(id: number, token: string) {
  const res = await fetch(`${API_URL}/library/${id}`, {
    method: "DELETE",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}
