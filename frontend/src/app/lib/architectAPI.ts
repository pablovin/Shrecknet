import { API_URL } from "./config";

export type ArchitectProposal = {
  id: string;
  proposal_type: "new_instance" | "update_instance";
  status: "pending" | "approved" | "rejected";
  entity_definition_id: number | null;
  entity_instance_id: string | null;
  alias: string | null;
  confidence: number | null;
  justification: string | null;
  metadata: Record<string, unknown> | null;
  proposal_metadata?: Record<string, unknown> | null;
  evidence: Array<Record<string, unknown>> | null;
  created_at: string;
  updated_at: string;
};

export type ArchitectRunSummary = {
  id: string;
  agent_id: string | null;
  background_job_id: number | null;
  ontology_id: number | null;
  ontology_instance_id: string;
  status: "pending" | "running" | "completed" | "failed";
  input_chunk_count: number | null;
  created_at: string;
  updated_at: string;
};

export type ArchitectRun = ArchitectRunSummary & {
  settings?: Record<string, unknown> | null;
  proposals: ArchitectProposal[];
};

type AnalysisPayload = {
  ontology_instance_id: string;
  ontology_id?: number | null;
  max_chunks?: number | null;
  chunk_size?: number | null;
};

async function apiFetch<T>(
  url: string,
  token: string | null | undefined,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw detail;
  }
  return (await res.json()) as T;
}

export async function startArchitectAnalysis(
  agentId: number,
  payload: AnalysisPayload,
  token: string,
): Promise<ArchitectRun> {
  return apiFetch<ArchitectRun>(
    `${API_URL}/jobs/architect/${agentId}/analyze`,
    token,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export async function listArchitectRuns(
  agentId: number,
  token: string,
  params: { limit?: number; offset?: number } = {},
): Promise<ArchitectRunSummary[]> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", `${params.limit}`);
  if (params.offset !== undefined) search.set("offset", `${params.offset}`);
  const query = search.toString();
  const url = `${API_URL}/jobs/architect/${agentId}/runs${
    query ? `?${query}` : ""
  }`;
  return apiFetch<ArchitectRunSummary[]>(url, token);
}

export async function getArchitectRun(
  runId: string,
  token: string,
): Promise<ArchitectRun> {
  return apiFetch<ArchitectRun>(
    `${API_URL}/jobs/architect/runs/${runId}`,
    token,
  );
}

export async function updateArchitectProposalStatus(
  runId: string,
  proposalIds: string[],
  status: "pending" | "approved" | "rejected",
  token: string,
): Promise<{ updated: number }> {
  return apiFetch<{ updated: number }>(
    `${API_URL}/jobs/architect/runs/${runId}/proposals/status`,
    token,
    {
      method: "PATCH",
      body: JSON.stringify({
        proposal_ids: proposalIds,
        status,
      }),
    },
  );
}
