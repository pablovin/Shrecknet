import { API_URL } from "./config";

export async function getSessions(
  tableId: number,
  token: string,
  joined = false,
) {
  const url = joined
    ? `${API_URL}/tables/${tableId}/sessions?joined=true`
    : `${API_URL}/tables/${tableId}/sessions`;
  const res = await fetch(url, {
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
  timezone: string,
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
      body: JSON.stringify({ proposed_times: proposedTimes, timezone }),
    },
  );
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function voteSessionPoll(
  tableId: number,
  sessionId: number,
  optionIds: number[],
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
      body: JSON.stringify({ option_ids: optionIds }),
    },
  );
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function removeSessionPollVote(
  tableId: number,
  sessionId: number,
  userId: number,
  optionId: number,
  token: string,
) {
  const res = await fetch(
    `${API_URL}/tables/${tableId}/sessions/${sessionId}/poll/vote/${userId}/${optionId}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
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

export async function updateSession(
  tableId: number,
  sessionId: number,
  data: unknown,
  token: string,
) {
  const res = await fetch(
    `${API_URL}/tables/${tableId}/sessions/${sessionId}`,
    {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(data),
    },
  );
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function deleteSession(
  tableId: number,
  sessionId: number,
  token: string,
) {
  const res = await fetch(
    `${API_URL}/tables/${tableId}/sessions/${sessionId}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  if (!res.ok) throw await res.json();
  return await res.json();
}

export async function syncCalendarSessions(
  tableId: number,
  token: string,
) {
  const res = await fetch(
    `${API_URL}/games/${tableId}/sessions/sync-calendar`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  if (!res.ok) throw await res.json();
  return await res.json();
}
