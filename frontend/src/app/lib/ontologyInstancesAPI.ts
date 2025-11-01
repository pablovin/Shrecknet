import { API_URL } from "./config";

export type OntologyInstance = {
  instance_id: string;
  ontology_id: number;
  name: string;
  created_at?: string;
  updated_at?: string;
  entities?: Array<Record<string, unknown>>;
};

type ListParams = {
  ontology_id?: number;
  skip?: number;
  limit?: number;
  search?: string;
};

async function apiFetch<T>(
  url: string,
  token: string,
): Promise<T> {
  const res = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    const detail = await res.text();
    throw detail;
  }
  return (await res.json()) as T;
}

export async function listOntologyInstances(
  token: string,
  params: ListParams,
): Promise<OntologyInstance[]> {
  const search = new URLSearchParams();
  if (params.ontology_id !== undefined) {
    search.set("ontology_id", `${params.ontology_id}`);
  }
  if (params.limit !== undefined) {
    search.set("limit", `${params.limit}`);
  }
  if (params.skip !== undefined) {
    search.set("skip", `${params.skip}`);
  }
  if (params.search) {
    search.set("search", params.search);
  }
  const query = search.toString();
  return apiFetch<OntologyInstance[]>(
    `${API_URL}/ontology-instances${query ? `?${query}` : ""}`,
    token,
  );
}
