import { API_URL } from "./config";

export async function listJobs(token: string) {
  const res = await fetch(`${API_URL}/jobs`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function deleteJobs(jobs: { kind: string; job_id: string }[], token: string) {
  const res = await fetch(`${API_URL}/jobs`, {
    method: "DELETE",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ jobs }),
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}
