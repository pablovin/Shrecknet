import { API_URL } from "./config";

export async function getNews(token: string) {
  const res = await fetch(`${API_URL}/news`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Failed to fetch news");
  return res.json();
}

export async function createNews(data: any, token: string) {
  const res = await fetch(`${API_URL}/news`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create news");
  return res.json();
}

export async function markNewsSeen(id: number, token: string) {
  const res = await fetch(`${API_URL}/news/${id}/seen`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Failed to mark news seen");
  return res.json();
}
