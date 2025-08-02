"use client";
import { useState } from "react";
import { useParams } from "next/navigation";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useSessions } from "@/app/lib/useSessions";
import { createSession } from "@/app/lib/sessionAPI";
import { useAuth } from "@/app/components/auth/AuthProvider";
import M3FloatingInput from "@/app/components/template/M3FloatingInput";

export default function TableSessionsPage() {
  const params = useParams();
  const tableId = Number(params?.id);
  const { token } = useAuth();
  const { sessions, mutate } = useSessions(tableId);
  const [open, setOpen] = useState(false);
  const [time, setTime] = useState("");
  const [location, setLocation] = useState("");
  const [summary, setSummary] = useState("");

  async function handleCreate() {
    await createSession(tableId, { scheduled_time: time, location, summary }, token);
    setOpen(false);
    setTime("");
    setLocation("");
    setSummary("");
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
            <li key={s.id} className="p-4 rounded-xl bg-[var(--surface-variant)]">
              <div className="font-semibold">{new Date(s.scheduled_time).toLocaleString()}</div>
              {s.location && <div className="text-sm">{s.location}</div>}
              {s.summary && <div className="text-sm opacity-80">{s.summary}</div>}
            </li>
          ))}
        </ul>
        {open && (
          <div className="fixed inset-0 bg-black/40 flex items-center justify-center">
            <div className="bg-[var(--surface)] p-6 rounded-xl w-full max-w-md space-y-4">
              <M3FloatingInput type="datetime-local" label="Time" value={time} onChange={(e: any) => setTime(e.target.value)} />
              <M3FloatingInput label="Location" value={location} onChange={(e: any) => setLocation(e.target.value)} />
              <M3FloatingInput label="Summary" value={summary} onChange={(e: any) => setSummary(e.target.value)} />
              <div className="flex justify-end gap-2">
                <button onClick={() => setOpen(false)} className="px-4 py-2 rounded-lg">
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
      </div>
    </DashboardLayout>
  );
}
