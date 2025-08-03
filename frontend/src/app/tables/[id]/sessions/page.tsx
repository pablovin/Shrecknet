"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useSessions } from "@/app/lib/useSessions";
import {
  createSession,
  createSessionPoll,
  finalizeSessionPoll,
} from "@/app/lib/sessionAPI";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { M3FloatingInput } from "@/app/components/template/M3FloatingInput";
import PageRefSelectorMD3 from "@/app/components/create_page/PageRefSelectorMD3";
import { getPages } from "@/app/lib/pagesAPI";
import { useUsers } from "@/app/lib/useUsers";
import { useSessionPoll } from "@/app/lib/useSessionPoll";
import { CalendarDays } from "lucide-react";

export default function TableSessionsPage() {
  const params = useParams();
  const tableId = Number(params?.id);
  const { token } = useAuth();
  const { sessions, mutate } = useSessions(tableId);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [time, setTime] = useState("");
  const [location, setLocation] = useState("");
  const [summary, setSummary] = useState("");
  const [pages, setPages] = useState<string[]>([]);
  const [pageOptions, setPageOptions] = useState<any[]>([]);
  const { users } = useUsers();
  const [pollSession, setPollSession] = useState<number | null>(null);
  const { poll, mutate: mutatePoll } = useSessionPoll(
    tableId,
    pollSession || 0,
  );
  const [proposed, setProposed] = useState<string[]>([]);
  const [newProposal, setNewProposal] = useState("");

  useEffect(() => {
    if (token) {
      getPages(token)
        .then(setPageOptions)
        .catch(() => setPageOptions([]));
    }
  }, [token]);

  async function handleCreate() {
    await createSession(
      tableId,
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
    setOpen(false);
    setName("");
    setTime("");
    setLocation("");
    setSummary("");
    setPages([]);
    mutate();
  }

  return (
    <DashboardLayout>
      <div className="w-full max-w-4xl mx-auto p-4">
        <div className="flex justify-between items-center mb-4">
          <h1 className="text-2xl font-bold">Sessions</h1>
          <button
            className="px-4 py-2 rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)]"
            onClick={() => setOpen(true)}
          >
            Schedule
          </button>
        </div>
        <ul className="space-y-2">
          {sessions.map((s: any) => (
            <li
              key={s.id}
              className="p-4 rounded-xl bg-[var(--surface-variant)] space-y-1"
            >
              <div className="flex justify-between items-center">
                <div>
                  <div className="font-semibold">{s.name}</div>
                  <div className="text-sm">
                    {new Date(s.scheduled_time).toLocaleString()}
                  </div>
                </div>
                <button
                  className="text-sm text-[var(--primary)] flex items-center gap-1"
                  onClick={() => setPollSession(s.id)}
                >
                  <CalendarDays className="w-4 h-4" /> Poll
                </button>
              </div>
              {s.location && <div className="text-sm">{s.location}</div>}
              {s.summary && (
                <div className="text-sm opacity-80">{s.summary}</div>
              )}
            </li>
          ))}
        </ul>
        {open && (
          <div className="fixed inset-0 bg-black/40 flex items-center justify-center">
            <div className="bg-[var(--surface)] p-6 rounded-xl w-full max-w-md space-y-4">
              <M3FloatingInput
                label="Name"
                value={name}
                onChange={(e: any) => setName(e.target.value)}
              />
              <M3FloatingInput
                type="datetime-local"
                label="Time"
                value={time}
                onChange={(e: any) => setTime(e.target.value)}
              />
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
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setOpen(false)}
                  className="px-4 py-2 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreate}
                  className="px-4 py-2 rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)]"
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        )}

        {pollSession && (
          <div className="fixed inset-0 bg-black/40 flex items-center justify-center">
            <div className="bg-[var(--surface)] p-6 rounded-xl w-full max-w-md space-y-4">
              {poll ? (
                <div className="space-y-4">
                  <h2 className="text-xl font-semibold">Session Poll</h2>
                  {poll.options.map((opt: any) => (
                    <div key={opt.id} className="border p-2 rounded">
                      <div className="flex justify-between items-center">
                        <div>
                          {new Date(opt.proposed_time).toLocaleString()}
                        </div>
                        {poll.final_option_id === null && (
                          <button
                            className="text-sm text-[var(--primary)]"
                            onClick={async () => {
                              await finalizeSessionPoll(
                                tableId,
                                pollSession,
                                opt.id,
                                token,
                              );
                              mutatePoll();
                              mutate();
                            }}
                          >
                            Finalize
                          </button>
                        )}
                      </div>
                      <div className="text-xs opacity-80 mt-1">
                        {opt.votes
                          .map(
                            (id: number) =>
                              users.find((u: any) => u.id === id)?.nickname ||
                              id,
                          )
                          .join(", ") || "No votes"}
                      </div>
                    </div>
                  ))}
                  <button
                    className="btn-outline w-full"
                    onClick={() => {
                      setPollSession(null);
                      mutatePoll();
                    }}
                  >
                    Close
                  </button>
                </div>
              ) : (
                <div className="space-y-4">
                  <h2 className="text-xl font-semibold">Create Poll</h2>
                  {proposed.map((p) => (
                    <div key={p} className="text-sm">
                      {new Date(p).toLocaleString()}
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
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={() => setPollSession(null)}
                      className="px-4 py-2 rounded-lg"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={async () => {
                        await createSessionPoll(
                          tableId,
                          pollSession,
                          proposed,
                          token,
                        );
                        setProposed([]);
                        mutatePoll();
                      }}
                      className="px-4 py-2 rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)]"
                    >
                      Save
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}
