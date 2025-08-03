"use client";
import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useSessions } from "@/app/lib/useSessions";
import {
  createSession,
  createSessionPoll,
  getSessionPoll,
  finalizeSessionPoll,
  updateSession,
  deleteSession,
} from "@/app/lib/sessionAPI";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { M3FloatingInput } from "@/app/components/template/M3FloatingInput";
import PageRefSelectorMD3 from "@/app/components/create_page/PageRefSelectorMD3";
import { getPages } from "@/app/lib/pagesAPI";
import { useUsers } from "@/app/lib/useUsers";
import {
  CalendarDays,
  Trash2,
  Pencil,
  MapPin,
  Users2,
  Book,
  Plus,
  ChevronDown,
  ChevronUp,
  ScrollText,
} from "lucide-react";
import Link from "next/link";
import Image from "next/image";

// --- Modal for new/edit session ---
function SessionModal({
  open,
  onClose,
  onSave,
  editSession,
  name,
  setName,
  time,
  setTime,
  location,
  setLocation,
  summary,
  setSummary,
  pages,
  setPages,
  usePoll,
  setUsePoll,
  proposed,
  setProposed,
  newProposal,
  setNewProposal,
  pageOptions,
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center">
      <div className="bg-white rounded-2xl shadow-2xl p-7 w-full max-w-lg relative">
        <h2 className="text-2xl font-bold font-serif text-[var(--primary)] mb-4 text-center">
          {editSession ? "Edit Session" : "Schedule a New Session"}
        </h2>
        <M3FloatingInput
          label="Name"
          value={name}
          onChange={(e: any) => setName(e.target.value)}
        />
        {!editSession && (
          <div className="flex gap-4 my-2">
            <label className="flex items-center gap-1 text-sm">
              <input
                type="radio"
                checked={!usePoll}
                onChange={() => setUsePoll(false)}
              />
              Set date
            </label>
            <label className="flex items-center gap-1 text-sm">
              <input
                type="radio"
                checked={usePoll}
                onChange={() => setUsePoll(true)}
              />
              Propose poll
            </label>
          </div>
        )}
        {(!usePoll || editSession) && (
          <M3FloatingInput
            type="datetime-local"
            label="Time"
            value={time}
            onChange={(e: any) => setTime(e.target.value)}
          />
        )}
        {usePoll && !editSession && (
          <div className="mb-3">
            <div className="mb-2 text-sm text-purple-900 bg-purple-50 rounded-lg px-2 py-1">
              Propose multiple possible dates and times for the group to vote on. Once everyone votes, a final date can be selected for the session.
            </div>
            {proposed.map((p, i) => (
              <div
                key={i}
                className="flex items-center justify-between text-xs bg-purple-50 rounded px-3 py-1 mb-1"
              >
                {new Date(p).toLocaleString()}
                <button
                  className="text-rose-500 hover:text-rose-700"
                  onClick={() =>
                    setProposed((prev) => prev.filter((_, idx) => idx !== i))
                  }
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
            <div className="flex gap-2 items-center">
              <M3FloatingInput
                type="datetime-local"
                label="Proposed Time"
                value={newProposal}
                onChange={(e: any) => setNewProposal(e.target.value)}
              />
              <button
                className="btn-primary mt-6"
                onClick={() => {
                  if (newProposal) {
                    setProposed([...proposed, newProposal]);
                    setNewProposal("");
                  }
                }}
              >
                Add
              </button>
            </div>
          </div>
        )}
        <M3FloatingInput
          label="Location"
          value={location}
          onChange={(e: any) => setLocation(e.target.value)}
        />
        <M3FloatingInput
          label="Summary"
          value={summary}
          onChange={(e: any) => setSummary(e.target.value)}
        />
        <PageRefSelectorMD3
          options={pageOptions.map((p: any) => ({
            id: p.id,
            name: p.name,
            logo: p.image_url,
          }))}
          value={pages}
          onChange={setPages}
          label="Pages"
        />
        <div className="flex justify-end gap-2 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg font-semibold text-[var(--primary)] hover:bg-[var(--primary)]/10 transition"
          >
            Cancel
          </button>
          <button
            onClick={onSave}
            className="px-4 py-2 rounded-lg bg-[var(--primary)] text-white font-bold shadow hover:bg-[var(--primary-dark)] transition"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

function PollModal({
  open,
  onClose,
  onSave,
  proposed,
  setProposed,
  newProposal,
  setNewProposal,
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center">
      <div className="bg-white rounded-2xl shadow-2xl p-6 w-full max-w-md relative">
        <h2 className="text-xl font-bold text-[var(--primary)] mb-4 text-center">Create Poll</h2>
        {proposed.map((p, i) => (
          <div
            key={i}
            className="flex items-center justify-between text-xs bg-purple-50 rounded px-3 py-1 mb-1"
          >
            {new Date(p).toLocaleString()}
            <button
              className="text-rose-500 hover:text-rose-700"
              onClick={() =>
                setProposed((prev) => prev.filter((_, idx) => idx !== i))
              }
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        ))}
        <div className="flex gap-2 items-center">
          <M3FloatingInput
            type="datetime-local"
            label="Proposed Time"
            value={newProposal}
            onChange={(e: any) => setNewProposal(e.target.value)}
          />
          <button
            className="btn-primary mt-6"
            onClick={() => {
              if (newProposal) {
                setProposed([...proposed, newProposal]);
                setNewProposal("");
              }
            }}
          >
            Add
          </button>
        </div>
        <div className="flex justify-end gap-2 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={onSave}
            className="px-4 py-2 rounded-lg bg-[var(--primary)] text-white font-bold shadow hover:bg-[var(--primary-dark)] transition"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

export default function TableSessionsPage() {
  const params = useParams();
  const tableId = Number(params?.id);
  const { token } = useAuth();
  const { sessions, mutate } = useSessions(tableId);

  // --- Modal state ---
  const [modalOpen, setModalOpen] = useState(false);
  const [editSession, setEditSession] = useState<any | null>(null);
  const [name, setName] = useState("");
  const [time, setTime] = useState("");
  const [location, setLocation] = useState("");
  const [summary, setSummary] = useState("");
  const [pages, setPages] = useState<string[]>([]);
  const [usePoll, setUsePoll] = useState(false);
  const [proposed, setProposed] = useState<string[]>([]);
  const [newProposal, setNewProposal] = useState("");
  const [pageOptions, setPageOptions] = useState<any[]>([]);

  // Poll Modal
  const [pollSession, setPollSession] = useState<number | null>(null);
  const [pollProposed, setPollProposed] = useState<string[]>([]);
  const [pollNewProposal, setPollNewProposal] = useState("");

  const { users } = useUsers();

  const [polls, setPolls] = useState<Record<number, any>>({});
  const [showPast, setShowPast] = useState(false);

  useEffect(() => {
    if (token) {
      getPages(token)
        .then(setPageOptions)
        .catch(() => setPageOptions([]));
    }
  }, [token]);

  useEffect(() => {
    async function loadPolls() {
      if (!token) return;
      const res: Record<number, any> = {};
      for (const s of sessions) {
        try {
          const p = await getSessionPoll(tableId, s.id, token);
          res[s.id] = p;
        } catch {
          // no poll
        }
      }
      setPolls(res);
    }
    loadPolls();
  }, [sessions, token, tableId]);

  async function refreshPoll(sessionId: number) {
    if (!token) return;
    try {
      const p = await getSessionPoll(tableId, sessionId, token);
      setPolls((prev) => ({ ...prev, [sessionId]: p }));
    } catch {
      setPolls((prev) => {
        const updated = { ...prev };
        delete updated[sessionId];
        return updated;
      });
    }
  }

  async function handleCreate() {
    const sess = await createSession(
      tableId,
      {
        name,
        scheduled_time: usePoll ? null : time,
        location,
        summary,
        page_ids: pages.map((p) => Number(p)),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      },
      token,
    );
    if (usePoll && proposed.length) {
      await createSessionPoll(tableId, sess.id, proposed, token);
      await refreshPoll(sess.id);
    }
    resetModal();
    mutate();
  }

  async function handleUpdate() {
    await updateSession(
      tableId,
      editSession!.id,
      {
        name,
        scheduled_time: time,
        location,
        summary,
        page_ids: pages.map((p) => Number(p)),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      },
      token,
    );
    resetModal();
    mutate();
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this session?")) return;
    await deleteSession(tableId, id, token);
    mutate();
  }

  function startEdit(s: any) {
    setEditSession(s);
    setName(s.name || "");
    setTime(s.scheduled_time ? s.scheduled_time.slice(0, 16) : "");
    setLocation(s.location || "");
    setSummary(s.summary || "");
    setPages((s.page_ids || []).map(String));
    setUsePoll(false);
    setProposed([]);
    setNewProposal("");
    setModalOpen(true);
  }

  function resetModal() {
    setModalOpen(false);
    setEditSession(null);
    setName("");
    setTime("");
    setLocation("");
    setSummary("");
    setPages([]);
    setUsePoll(false);
    setProposed([]);
    setNewProposal("");
  }

  // --- Poll Modal logic ---
  function openPollModal(sessionId: number) {
    setPollSession(sessionId);
    setPollProposed([]);
    setPollNewProposal("");
  }
  async function savePollModal() {
    if (!pollSession) return;
    await createSessionPoll(tableId, pollSession, pollProposed, token);
    await refreshPoll(pollSession);
    setPollSession(null);
    setPollProposed([]);
    setPollNewProposal("");
    mutate();
  }

  // --- Sessions ---
  const now = new Date();
  const upcoming = sessions.filter(
    (s: any) => !s.scheduled_time || new Date(s.scheduled_time) >= now,
  );
  const past = sessions
    .filter((s: any) => s.scheduled_time && new Date(s.scheduled_time) < now)
    .sort((a, b) => new Date(b.scheduled_time) - new Date(a.scheduled_time));

  // --- Card components ---
  function SessionCard({ s, pollData }: { s: any; pollData?: any }) {
    const isPast = s.scheduled_time && new Date(s.scheduled_time) < now;
    return (
      <div
        className={`
          group rounded-2xl border border-[var(--border)] bg-white shadow-lg px-5 py-4 mb-3 min-w-[320px] max-w-md
          transition relative flex flex-col gap-2 hover:shadow-2xl
          ${isPast ? "opacity-70" : "opacity-100"}
        `}
      >
        <div className="flex justify-between items-center mb-1">
          <div>
            <div className="font-bold font-serif text-lg text-[var(--primary)] flex items-center gap-2">
              <ScrollText className="w-5 h-5" /> {s.name}
              <span className={`ml-2 px-2 py-0.5 rounded-full text-xs font-bold
                ${isPast ? "bg-gray-200 text-gray-500" : pollData ? "bg-yellow-100 text-yellow-700" : "bg-green-100 text-green-700"}
              `}>
                {isPast ? "Completed" : pollData ? "Vote" : "Scheduled"}
              </span>
            </div>
            <div className="text-xs text-gray-600 flex items-center gap-1">
              <CalendarDays className="w-4 h-4" />
              {s.scheduled_time
                ? new Date(s.scheduled_time).toLocaleString()
                : pollData
                ? <span className="font-semibold text-yellow-600">Voting!</span>
                : <span className="font-semibold text-gray-500">Unscheduled</span>}
            </div>
          </div>
          <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition">
            <button
              title="Edit"
              className="p-1 rounded-full hover:bg-[var(--primary)]/10"
              onClick={() => startEdit(s)}
            >
              <Pencil className="w-4 h-4 text-[var(--primary)]" />
            </button>
            <button
              title="Delete"
              className="p-1 rounded-full hover:bg-rose-50"
              onClick={() => handleDelete(s.id)}
            >
              <Trash2 className="w-4 h-4 text-rose-500" />
            </button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {s.location && (
            <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-purple-100 text-purple-700 text-xs font-semibold">
              <MapPin className="w-3 h-3" /> {s.location}
            </span>
          )}
          {s.summary && (
            <span className="text-xs text-gray-500 italic">{s.summary}</span>
          )}
        </div>
        <div className="flex flex-wrap gap-2 items-center mt-2">
          {s.page_ids?.length > 0 && (
            <span className="flex items-center gap-2 text-xs text-gray-600">
              <Book className="w-4 h-4" />
              Linked:{" "}
              {s.page_ids.map((pid: number) => {
                const p = pageOptions.find((pg) => pg.id === pid);
                return p ? (
                  <Link
                    key={pid}
                    href={`/worlds/${p.world_id || p.gameworld_id}/concept/${p.concept_id}/page/${pid}`}
                    className="underline text-[var(--primary)] font-semibold hover:text-[var(--accent)] transition"
                    target="_blank"
                  >
                    {p.name}
                  </Link>
                ) : null;
              })}
            </span>
          )}
        </div>
        {/* Poll UI */}
        {pollData && (
          <div className="mt-2">
            <div className="text-xs font-semibold mb-1">Session Date Poll:</div>
            <div className="flex flex-wrap gap-2 mb-1">
              {pollData.options.map((opt: any) => (
                <div
                  key={opt.id}
                  className={`flex flex-col items-center px-3 py-2 rounded-xl border
                    ${pollData.final_option_id === opt.id ? "bg-green-100 border-green-300" : "bg-yellow-50 border-yellow-200"}
                  `}
                >
                  <span className="font-bold text-xs">{new Date(opt.proposed_time).toLocaleString()}</span>
                  <div className="flex -space-x-2 mt-1">
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
                  {pollData.final_option_id === null && (
                    <button
                      className="mt-2 px-2 py-1 bg-[var(--primary)] text-white rounded text-xs shadow hover:bg-[var(--accent)]"
                      onClick={async () => {
                        await finalizeSessionPoll(tableId, s.id, opt.id, token);
                        mutate();
                        refreshPoll(s.id);
                      }}
                    >
                      Select
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
        {!isPast && !pollData && (
          <button
            className="mt-2 text-xs text-[var(--primary)] underline"
            onClick={() => openPollModal(s.id)}
          >
            Create Poll
          </button>
        )}
      </div>
    );
  }

  // -- Main render --
  return (
    <DashboardLayout>
      <div className="w-full max-w-5xl mx-auto px-3 py-10 min-h-screen relative">
        {/* --- Party/Adventure header (optional, for style) --- */}
        <div className="flex items-center gap-3 mb-10">
          <Users2 className="w-8 h-8 text-[var(--primary)]" />
          <h1 className="text-2xl font-bold font-serif text-[var(--primary)]">Game Sessions</h1>
          <button
            className="ml-auto flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[var(--primary)] text-white font-bold shadow-lg border-2 border-[var(--primary)] hover:bg-[var(--primary-dark)] transition"
            onClick={() => {
              setEditSession(null);
              setName("");
              setTime("");
              setLocation("");
              setSummary("");
              setPages([]);
              setUsePoll(false);
              setProposed([]);
              setNewProposal("");
              setModalOpen(true);
            }}
          >
            <Plus className="w-5 h-5" />
            New Session
          </button>
        </div>

        {/* --- Upcoming Sessions --- */}
        <div>
          <div className="font-bold font-serif text-xl text-[var(--primary)] mb-2 flex items-center gap-2">
            <CalendarDays className="w-6 h-6" />
            Upcoming Sessions
          </div>
          <div className="flex flex-row gap-6 overflow-x-auto pb-4">
            {upcoming.length === 0 ? (
              <div className="text-gray-500 italic mt-2">No upcoming sessions.</div>
            ) : (
              upcoming.map((s: any) => (
                <SessionCard key={s.id} s={s} pollData={polls[s.id]} />
              ))
            )}
          </div>
        </div>

        {/* --- Past Sessions --- */}
        <div className="mt-8">
          <button
            className="flex items-center gap-2 text-[var(--primary)] font-semibold mb-2"
            onClick={() => setShowPast((v) => !v)}
          >
            {showPast ? <ChevronUp /> : <ChevronDown />}
            Past Sessions
          </button>
          {showPast && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
              {past.length === 0 ? (
                <div className="text-gray-400 italic ml-2">No past sessions.</div>
              ) : (
                past.map((s: any) => (
                  <SessionCard key={s.id} s={s} pollData={polls[s.id]} />
                ))
              )}
            </div>
          )}
        </div>

        {/* --- Modals --- */}
        <SessionModal
          open={modalOpen}
          onClose={resetModal}
          onSave={editSession ? handleUpdate : handleCreate}
          editSession={editSession}
          name={name}
          setName={setName}
          time={time}
          setTime={setTime}
          location={location}
          setLocation={setLocation}
          summary={summary}
          setSummary={setSummary}
          pages={pages}
          setPages={setPages}
          usePoll={usePoll}
          setUsePoll={setUsePoll}
          proposed={proposed}
          setProposed={setProposed}
          newProposal={newProposal}
          setNewProposal={setNewProposal}
          pageOptions={pageOptions}
        />
        <PollModal
          open={!!pollSession}
          onClose={() => setPollSession(null)}
          onSave={savePollModal}
          proposed={pollProposed}
          setProposed={setPollProposed}
          newProposal={pollNewProposal}
          setNewProposal={setPollNewProposal}
        />
      </div>
    </DashboardLayout>
  );
}
