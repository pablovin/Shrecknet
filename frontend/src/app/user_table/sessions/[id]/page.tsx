"use client";
import { useParams } from "next/navigation";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useSessions } from "@/app/lib/useSessions";

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
  const { sessions } = useSessions(tableId);

  return (
    <DashboardLayout>
      <div className="w-full max-w-4xl mx-auto p-4">
        <h1 className="text-2xl font-bold mb-4">Sessions</h1>
        <ul className="space-y-2">
          {sessions.map((s: SessionItem) => (
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
              {s.summary && (
                <div className="text-sm opacity-80">{s.summary}</div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </DashboardLayout>
  );
}
