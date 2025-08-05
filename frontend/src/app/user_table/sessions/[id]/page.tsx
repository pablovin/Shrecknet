"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useSessions } from "@/app/lib/useSessions";
import { getSessionPoll, voteSessionPoll } from "@/app/lib/sessionAPI";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { useUsers } from "@/app/lib/useUsers";
// import icons (Heroicons/Lucide etc)
import { Sparkles, MapPin, CalendarClock, BookOpen, User } from "lucide-react";

export default function UserTableSessionsPage() {
  const params = useParams();
  const tableId = Number(params?.id);
  const { token, user } = useAuth();
  const { sessions } = useSessions(tableId, true);
  const { users } = useUsers();
  const [polls, setPolls] = useState({});
  const [selections, setSelections] = useState({});
  const [loadingVotes, setLoadingVotes] = useState({}); // to show loading

  useEffect(() => {
    async function loadPolls() {
      if (!token || !user) return;
      const res = {};
      const sel = {};
      for (const s of sessions) {
        try {
          const p = await getSessionPoll(tableId, s.id, token);
          res[s.id] = p;
          sel[s.id] = p.options
            .filter((o) => o.votes.includes(user.id))
            .map((o) => o.id);
        } catch {/* no poll */}
      }
      setPolls(res);
      setSelections(sel);
    }
    loadPolls();
  }, [sessions, token, tableId, user]);

  function toggleOption(sessionId, optionId) {
    setSelections((prev) => {
      const current = prev[sessionId] || [];
      return current.includes(optionId)
        ? { ...prev, [sessionId]: current.filter((id) => id !== optionId) }
        : { ...prev, [sessionId]: [...current, optionId] };
    });
  }

  async function refreshPoll(sessionId) {
    if (!token || !user) return;
    try {
      const p = await getSessionPoll(tableId, sessionId, token);
      setPolls((prev) => ({ ...prev, [sessionId]: p }));
      setSelections((prev) => ({
        ...prev,
        [sessionId]: p.options
          .filter((o) => o.votes.includes(user.id))
          .map((o) => o.id),
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

  async function submitVote(sessionId) {
    if (!token) return;
    setLoadingVotes((prev) => ({ ...prev, [sessionId]: true }));
    await voteSessionPoll(
      tableId,
      sessionId,
      selections[sessionId] || [],
      token
    );
    await refreshPoll(sessionId);
    setLoadingVotes((prev) => ({ ...prev, [sessionId]: false }));
  }

  const now = new Date();
  const upcomingSessions = sessions.filter(
    (s) => !s.scheduled_time || new Date(s.scheduled_time) >= now
  );
  const previousSessions = sessions.filter(
    (s) => s.scheduled_time && new Date(s.scheduled_time) < now
  );

  // --- UI HELPERS ---

  function renderPoll(s) {
    const poll = polls[s.id];
    if (!poll) return null;

    return (
      <div className="mt-3 bg-yellow-50/90 rounded-xl border-2 border-yellow-200 p-3 shadow-inner">
        <div className="flex items-center gap-2 mb-2 font-semibold text-yellow-700">
          <Sparkles size={18} className="text-yellow-400" />
          Session Date Poll
        </div>
        <div className="flex flex-col gap-2 mb-2">
          {poll.options.map((opt) => (
            <label
              key={opt.id}
              className={`flex items-center gap-3 rounded-xl px-3 py-2 border cursor-pointer 
              ${selections[s.id]?.includes(opt.id)
                  ? "border-[var(--primary)] bg-[var(--primary)]/10"
                  : "border-yellow-100 hover:border-yellow-300"}
              transition`}
            >
              <input
                type="checkbox"
                checked={selections[s.id]?.includes(opt.id) || false}
                onChange={() => toggleOption(s.id, opt.id)}
                className="w-5 h-5 accent-[var(--primary)] rounded-md border-2"
              />
              <span className="font-mono text-xs">
                {new Date(opt.proposed_time).toLocaleString()}
              </span>
              <div className="flex -space-x-2">
                {opt.votes.map((uid) => {
                  const u = users.find((usr) => usr.id === uid);
                  return (
                    <img
                      key={uid}
                      src={u?.image_url || "/images/avatars/default.png"}
                      alt={u?.nickname || "?"}
                      className="w-7 h-7 rounded-full border-2 border-white bg-white shadow"
                      title={u?.nickname}
                    />
                  );
                })}
              </div>
              <span className="text-xs text-yellow-600 ml-2">
                {opt.votes.length} vote{opt.votes.length === 1 ? "" : "s"}
              </span>
            </label>
          ))}
        </div>
        <button
          className={`mt-2 px-4 py-2 rounded-lg bg-[var(--primary)] text-white font-bold shadow 
            hover:scale-105 transition 
            ${loadingVotes[s.id] ? "opacity-60 cursor-not-allowed" : ""}
          `}
          disabled={loadingVotes[s.id]}
          onClick={() => submitVote(s.id)}
        >
          {loadingVotes[s.id] ? "Saving..." : "Cast My Vote"}
        </button>
      </div>
    );
  }

  function renderSession(s, isPast = false) {
    return (
      <li
        key={s.id}
        className={`relative bg-[var(--surface-variant)] border-2 border-[var(--primary)]/30 shadow-xl rounded-2xl px-6 py-5 flex flex-col gap-2 
          transition hover:border-[var(--primary)]/60`}
      >
        <div className="flex items-center gap-4">
          <div className="flex-1">
            <div className="text-xl font-bold text-[var(--primary)] flex items-center gap-2">
              {isPast ? <BookOpen size={20} className="text-gray-400" /> : <Sparkles size={20} className="text-yellow-400" />}
              {s.name}
            </div>
            <div className="flex items-center gap-3 text-sm text-gray-700 mt-1">
              <MapPin size={16} className="text-[var(--primary)]" />
              {s.location || <span className="italic opacity-50">No location set</span>}
              <CalendarClock size={16} className="ml-3 text-[var(--primary)]" />
              {s.scheduled_time
                ? new Date(s.scheduled_time).toLocaleString()
                : <span className="italic opacity-50">Not scheduled</span>
              }
            </div>
            {s.summary && (
              <div className="text-xs text-[var(--primary)]/70 mt-2 px-3 py-1 bg-[var(--surface)]/60 rounded shadow-inner border-l-4 border-[var(--primary)]/20">
                {s.summary}
              </div>
            )}
          </div>
        </div>
        {!isPast && renderPoll(s)}
      </li>
    );
  }

  // ---- FINAL RENDER ----

  return (
    <DashboardLayout>
      <div className="w-full max-w-3xl mx-auto p-6">
        <div className="mb-8">
          <h1 className="text-4xl font-extrabold text-[var(--primary)] mb-1 flex items-center gap-3">
            <Sparkles size={36} className="text-yellow-300" />
            Game Sessions
          </h1>
          <div className="text-md text-gray-500 ml-2">
            Track your table's adventures, vote on the next gathering, and relive the tales of previous quests!
          </div>
        </div>

        <section className="mb-10">
          <h2 className="text-2xl font-bold text-[var(--primary)] flex items-center gap-2 mb-2">
            <CalendarClock size={22} className="text-yellow-500" />
            Upcoming Sessions
          </h2>
          {upcomingSessions.length === 0 ? (
            <div className="text-gray-400 italic mb-4">No upcoming sessions scheduled. Ask your Game Master to create one!</div>
          ) : (
            <ul className="space-y-4">
              {upcomingSessions.map((s) => renderSession(s, false))}
            </ul>
          )}
        </section>

        <section>
          <h2 className="text-2xl font-bold text-[var(--primary)] flex items-center gap-2 mb-2 mt-10">
            <BookOpen size={22} className="text-purple-500" />
            Past Sessions
          </h2>
          {previousSessions.length === 0 ? (
            <div className="text-gray-400 italic mb-2">No past sessions yet. Your journey begins now!</div>
          ) : (
            <ul className="space-y-4">
              {previousSessions.map((s) => renderSession(s, true))}
            </ul>
          )}
        </section>
      </div>
    </DashboardLayout>
  );
}
