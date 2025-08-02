import { API_URL } from "./config";

export async function getSessions(tableId: number, token: string) {
  const res = await fetch(`${API_URL}/tables/${tableId}/sessions`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Failed to fetch sessions");
  return await res.json();
}

export async function createSession(
  tableId: number,
  data: unknown,
  token: string,
) {
  const res = await fetch(`${API_URL}/tables/${tableId}/sessions`, {
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

export async function getSessionPoll(
  tableId: number,
  sessionId: number,
  token: string,
) {
  const res = await fetch(
    `${API_URL}/tables/${tableId}/sessions/${sessionId}/poll`,
    {
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  if (!res.ok) throw new Error("Failed to fetch poll");
  return await res.json();
}

export async function createSessionPoll(
  tableId: number,
  sessionId: number,
  proposedTimes: string[],
  token: string,
) {
  const res = await fetch(
    `${API_URL}/tables/${tableId}/sessions/${sessionId}/poll`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ proposed_times: proposedTimes }),
    },
  );
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function voteSessionPoll(
  tableId: number,
  sessionId: number,
  optionId: number,
  token: string,
) {
  const res = await fetch(
    `${API_URL}/tables/${tableId}/sessions/${sessionId}/poll/vote`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ option_id: optionId }),
    },
  );
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function finalizeSessionPoll(
  tableId: number,
  sessionId: number,
  optionId: number,
  token: string,
) {
  const res = await fetch(
    `${API_URL}/tables/${tableId}/sessions/${sessionId}/poll/finalize`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ option_id: optionId }),
    },
  );
  if (!res.ok) throw await res.json();
  return await res.json();
}
