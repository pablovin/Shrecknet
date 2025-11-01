import { API_URL } from "./config";

export type Ontology = {
  id: number;
  name: string;
  description: string | null;
  image_url?: string | null;
  created_at?: string;
  updated_at?: string;
};

async function apiFetch<T>(url: string, token: string): Promise<T> {
  const res = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    const detail = await res.text();
    throw detail;
  }
  return (await res.json()) as T;
}

export async function getOntology(
  token: string,
  ontologyId: number,
): Promise<Ontology> {
  return apiFetch<Ontology>(`${API_URL}/ontologies/${ontologyId}`, token);
}
