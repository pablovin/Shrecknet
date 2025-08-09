/* eslint-disable @typescript-eslint/no-explicit-any */
import { API_URL } from "./config";

export type SourceLink = { title: string; url: string };

export type ChatMessage = {
  role: "system" | "user" | "assistant";
  content: string;
  sources?: SourceLink[];
};

export async function getAgents(
  token: string,
  params: { world_id?: number } = {},
) {
  const query = new URLSearchParams();
  if (params.world_id !== undefined)
    query.append("world_id", params.world_id.toString());
  const res = await fetch(`${API_URL}/agents/?${query.toString()}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Could not fetch agents");
  return await res.json();
}

export async function getAgent(id: number, token: string) {
  const res = await fetch(`${API_URL}/agents/${id}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Agent not found");
  return await res.json();
}

export async function createAgent(data: unknown, token: string) {
  const res = await fetch(`${API_URL}/agents/`, {
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

export async function updateAgent(id: number, data: unknown, token: string) {
  const res = await fetch(`${API_URL}/agents/${id}`, {
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

export async function deleteAgent(id: number, token: string) {
  const res = await fetch(`${API_URL}/agents/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function chatWithConversationalAgent(
  agentId: number,
  messages: ChatMessage[],
  token: string,
) {
  const res = await fetch(`${API_URL}/agents/${agentId}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ messages }),
  });

  if (!res.ok) throw await res.text();

  const data = await res.json();
  return data as { answer: string; sources: SourceLink[] };
}

export async function chatTest(
  agentId: number,
  messages: ChatMessage[],
  token: string,
) {
  const res = await fetch(`${API_URL}/agents/${agentId}/chat_test`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function getChatHistory(
  agentId: number,
  token: string,
  limit = 20,
) {
  const res = await fetch(
    `${API_URL}/agents/${agentId}/history?limit=${limit}`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    },
  );
  if (!res.ok) throw await res.text();
  const data = await res.json();
  return data.messages || [];
}

export async function clearChatHistory(agentId: number, token: string) {
  const res = await fetch(`${API_URL}/agents/${agentId}/history`, {
    method: "DELETE",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function analyzePageWithAgent(
  agentId: number,
  pageId: number,
  token: string,
) {
  const { job_id } = await startAnalyzeJob(agentId, [pageId], token);
  let attempts = 0;
  while (attempts < 30) {
    await new Promise((r) => setTimeout(r, 2000));
    const job = await getWriterJob(job_id, token);
    if (job.status === "done") {
      return { suggestions: job.suggestions || job.analysis || [] };
    }
    attempts++;
  }
  throw new Error("timeout");
}
export async function generatePagesWithAgent(
  agentId: number,
  pageId: number,
  pages: unknown[],
  token: string,
  mergeGroups?: string[][],
) {
  const body: any = { pages };
  if (mergeGroups) body.merge_groups = mergeGroups;
  const res = await fetch(
    `${API_URL}/agents/${agentId}/pages/${pageId}/generate`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function startAnalyzeJob(
  agentId: number,
  pageIds: number[],
  token: string,
) {
  const res = await fetch(`${API_URL}/agents/${agentId}/analyze_job`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ page_ids: pageIds }),
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function startGenerateJob(
  agentId: number,
  pageId: number,
  pages: any[],
  token: string,
  suggestions?: any[],
  mergeGroups?: string[][],
  requestId?: string,
) {
  const body: any = { pages };
  if (suggestions) body.suggestions = suggestions;
  if (mergeGroups) body.merge_groups = mergeGroups;
  if (requestId) body.request_id = requestId;
  const res = await fetch(
    `${API_URL}/agents/${agentId}/pages/${pageId}/generate_job`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function getWriterJob(jobId: string, token: string) {
  const res = await fetch(`${API_URL}/agents/writer_jobs/${jobId}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function listWriterJobs(token: string) {
  const res = await fetch(`${API_URL}/agents/writer_jobs`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function startNovelJob(
  agentId: number,
  data: {
    text: string;
    instructions: string;
    previous_page_id?: number | null;
    helper_agents?: number[];
  },
  token: string,
) {
  const res = await fetch(`${API_URL}/agents/${agentId}/novel_job`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function getNovelistJob(jobId: string, token: string) {
  const res = await fetch(`${API_URL}/agents/novelist_jobs/${jobId}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function listNovelistJobs(token: string) {
  const res = await fetch(`${API_URL}/agents/novelist_jobs`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await res.text();
  return await res.json();
}

export async function updateWriterJob(jobId: string, data: any, token: string) {
  const res = await fetch(`${API_URL}/agents/writer_jobs/${jobId}`, {
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

export async function updateNovelistJob(
  jobId: string,
  data: any,
  token: string,
) {
  const res = await fetch(`${API_URL}/agents/novelist_jobs/${jobId}`, {
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
