"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useSessions } from "@/app/lib/useSessions";
import { getSessionPoll, voteSessionPoll } from "@/app/lib/sessionAPI";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { useUsers } from "@/app/lib/useUsers";

interface SessionItem {
  id: number;
  name: string;
  scheduled_time: string | null;
  location?: string | null;
  summary?: string | null;
}

export default function UserTableSessionsPage() {
  const params = useParams();
  const tableId = Number(params?.id);
  const { token, user } = useAuth();
  const { sessions } = useSessions(tableId, true);
  const { users } = useUsers();
  const [polls, setPolls] = useState<Record<number, any>>({});
  const [selections, setSelections] = useState<Record<number, number[]>>({});

  useEffect(() => {
    async function loadPolls() {
      if (!token || !user) return;
      const res: Record<number, any> = {};
      const sel: Record<number, number[]> = {};
      for (const s of sessions) {
        try {
          const p = await getSessionPoll(tableId, s.id, token);
          res[s.id] = p;
          sel[s.id] = p.options
            .filter((o: any) => o.votes.includes(user.id))
            .map((o: any) => o.id);
        } catch {
          /* no poll */
        }
      }
      setPolls(res);
      setSelections(sel);
    }
    loadPolls();
  }, [sessions, token, tableId, user]);

  function toggleOption(sessionId: number, optionId: number) {
    setSelections((prev) => {
      const current = prev[sessionId] || [];
      return current.includes(optionId)
        ? { ...prev, [sessionId]: current.filter((id) => id !== optionId) }
        : { ...prev, [sessionId]: [...current, optionId] };
    });
  }

  async function refreshPoll(sessionId: number) {
    if (!token || !user) return;
    try {
      const p = await getSessionPoll(tableId, sessionId, token);
      setPolls((prev) => ({ ...prev, [sessionId]: p }));
      setSelections((prev) => ({
        ...prev,
        [sessionId]: p.options
          .filter((o: any) => o.votes.includes(user.id))
          .map((o: any) => o.id),
      }));
    } catch {
      setPolls((prev) => {
        const copy = { ...prev };
        delete copy[sessionId];
        return copy;
      });
      setSelections((prev) => {
        const copy = { ...prev };
        delete copy[sessionId];
        return copy;
      });
    }
  }

  async function submitVote(sessionId: number) {
    if (!token) return;
    await voteSessionPoll(
      tableId,
      sessionId,
      selections[sessionId] || [],
      token,
    );
    await refreshPoll(sessionId);
  }

  const now = new Date();
  const upcomingSessions = sessions.filter(
    (s: SessionItem) => !s.scheduled_time || new Date(s.scheduled_time) >= now,
  );
  const previousSessions = sessions.filter(
    (s: SessionItem) => s.scheduled_time && new Date(s.scheduled_time) < now,
  );

  const renderSession = (s: SessionItem) => (
    <li
      key={s.id}
      className="p-4 rounded-xl bg-[var(--surface-variant)] space-y-1"
    >
      <div className="flex justify-between items-center">
        <div>
          <div className="font-semibold">{s.name}</div>
          <div className="text-sm">
            {s.scheduled_time
              ? new Date(s.scheduled_time).toLocaleString()
              : "Not scheduled"}
          </div>
        </div>
      </div>
      {s.location && <div className="text-sm">{s.location}</div>}
      {s.summary && <div className="text-sm opacity-80">{s.summary}</div>}
      {polls[s.id] && (
        <div className="mt-2">
          <div className="text-xs font-semibold mb-1">Session Date Poll:</div>
          <div className="flex flex-wrap gap-2 mb-2">
            {polls[s.id].options.map((opt: any) => (
              <label
                key={opt.id}
                className="flex items-center gap-2 px-3 py-2 rounded-xl border bg-yellow-50 border-yellow-200"
              >
                <input
                  type="checkbox"
                  checked={(selections[s.id] || []).includes(opt.id)}
                  onChange={() => toggleOption(s.id, opt.id)}
                />
                <span className="text-xs font-semibold">
                  {new Date(opt.proposed_time).toLocaleString()}
                </span>
                <div className="flex -space-x-2">
                  {opt.votes.map((uid: number) => {
                    const u = users.find((usr: any) => usr.id === uid);
                    return (
                      <img
                        key={uid}
                        src={u?.image_url || "/images/avatars/default.png"}
                        alt={u?.nickname || "?"}
                        className="w-5 h-5 rounded-full border border-white bg-white shadow"
                        title={u?.nickname}
                      />
                    );
                  })}
                </div>
              </label>
            ))}
          </div>
          <button
            className="px-2 py-1 bg-[var(--primary)] text-white rounded text-xs"
            onClick={() => submitVote(s.id)}
          >
            Save Vote
          </button>
        </div>
      )}
    </li>
  );

  return (
    <DashboardLayout>
      <div className="w-full max-w-4xl mx-auto p-4">
        <h1 className="text-2xl font-bold mb-4">Sessions</h1>
        <h2 className="text-xl font-semibold mb-2">Upcoming Sessions</h2>
        <ul className="space-y-2">{upcomingSessions.map(renderSession)}</ul>
        {previousSessions.length > 0 && (
          <>
            <h2 className="text-xl font-semibold mt-6 mb-2">
              Previous Sessions
            </h2>
            <ul className="space-y-2">{previousSessions.map(renderSession)}</ul>
          </>
        )}
      </div>
    </DashboardLayout>
  );
}
