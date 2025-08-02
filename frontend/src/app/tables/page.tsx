"use client";
import { useState } from "react";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useTables } from "@/app/lib/useTables";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { createTable } from "@/app/lib/tableAPI";
import { M3FloatingInput } from "../components/template/M3FloatingInput";

export default function TablesPage() {
  const { tables, mutate } = useTables();
  const { token } = useAuth();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [worldId, setWorldId] = useState("");
  const [crest, setCrest] = useState("");

  async function handleCreate() {
    await createTable({ world_id: Number(worldId), name, crest_url: crest, member_ids: [] }, token);
    setOpen(false);
    setName("");
    setWorldId("");
    setCrest("");
    mutate();
  }

  return (
    <DashboardLayout>
      <div className="w-full max-w-4xl mx-auto p-4">
        <div className="flex justify-between items-center mb-4">
          <h1 className="text-2xl font-bold">Party Tables</h1>
          <button
            className="px-4 py-2 rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)]"
            onClick={() => setOpen(true)}
          >
            Create
          </button>
        </div>
        <ul className="space-y-2">
          {tables.map((t: any) => (
            <li key={t.id} className="p-4 rounded-xl bg-[var(--surface-variant)] flex justify-between">
              <span className="font-semibold">{t.name}</span>
              <a href={`/tables/${t.id}/sessions`} className="text-sm text-[var(--primary)]">
                Sessions
              </a>
            </li>
          ))}
        </ul>
        {open && (
          <div className="fixed inset-0 bg-black/40 flex items-center justify-center">
            <div className="bg-[var(--surface)] p-6 rounded-xl w-full max-w-md space-y-4">
              <M3FloatingInput label="Name" value={name} onChange={(e: any) => setName(e.target.value)} />
              <M3FloatingInput label="World ID" value={worldId} onChange={(e: any) => setWorldId(e.target.value)} />
              <M3FloatingInput label="Crest URL" value={crest} onChange={(e: any) => setCrest(e.target.value)} />
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
