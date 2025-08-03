"use client";
import { useState, ChangeEvent } from "react";
import Image from "next/image";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useTables } from "@/app/lib/useTables";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { createTable, updateTable } from "@/app/lib/tableAPI";
import { uploadTableLogo } from "@/app/lib/uploadTableLogo";
import PageRefSelectorMD3 from "@/app/components/create_page/PageRefSelectorMD3";
import { useWorlds } from "@/app/lib/userWorlds";
import { useUsers } from "@/app/lib/useUsers";
import { M3FloatingInput } from "../components/template/M3FloatingInput";

interface TableMember {
  id: number;
  nickname: string;
  image_url?: string | null;
}

interface TableItem {
  id: number;
  name: string;
  world_name: string;
  crest_url?: string | null;
  members: TableMember[];
  latest_session?: string | null;
  next_session?: string | null;
}

export default function TablesPage() {
  const { tables, mutate } = useTables();
  const { worlds } = useWorlds();
  const { users } = useUsers();
  const { token } = useAuth();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [worldId, setWorldId] = useState("");
  const [logo, setLogo] = useState<File | null>(null);
  const [members, setMembers] = useState<string[]>([]);

  async function handleCreate() {
    const table = await createTable(
      {
        world_id: Number(worldId),
        name,
        member_ids: members.map((m) => Number(m)),
      },
      token,
    );

    if (logo) {
      const url = await uploadTableLogo(logo, table.id);
      await updateTable(table.id, { crest_url: url }, token);
    }

    setOpen(false);
    setName("");
    setWorldId("");
    setLogo(null);
    setMembers([]);
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
          {tables.map((t: TableItem) => (
            <li
              key={t.id}
              className="p-4 rounded-xl bg-[var(--surface-variant)] flex gap-4"
            >
              <Image
                src={t.crest_url || "/images/worlds/new_game.png"}
                alt={t.name}
                width={64}
                height={64}
                className="w-16 h-16 rounded object-cover"
              />
              <div className="flex-1 space-y-1">
                <div className="flex justify-between">
                  <span className="font-semibold">{t.name}</span>
                  <span className="text-sm text-[var(--primary)]">
                    {t.world_name}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {t.members.map((m: TableMember) => (
                    <div key={m.id} className="flex items-center gap-1 text-xs">
                      <Image
                        src={m.image_url || "/images/avatars/default.png"}
                        alt={m.nickname}
                        width={24}
                        height={24}
                        className="w-6 h-6 rounded-full object-cover"
                      />
                      <span>{m.nickname}</span>
                    </div>
                  ))}
                </div>
                <div className="text-xs">
                  {t.latest_session && (
                    <div>
                      Last: {new Date(t.latest_session).toLocaleString()} (
                      {Math.floor(
                        (Date.now() - new Date(t.latest_session).getTime()) /
                          (1000 * 60 * 60 * 24),
                      )}{" "}
                      days ago)
                    </div>
                  )}
                  {t.next_session && (
                    <div>
                      Next: {new Date(t.next_session).toLocaleString()} (in{" "}
                      {Math.ceil(
                        (new Date(t.next_session).getTime() - Date.now()) /
                          (1000 * 60 * 60 * 24),
                      )}{" "}
                      days)
                    </div>
                  )}
                </div>
              </div>
              <a
                href={`/tables/${t.id}/sessions`}
                className="text-sm text-[var(--primary)] self-start"
              >
                Sessions
              </a>
            </li>
          ))}
        </ul>
        {open && (
          <div className="fixed inset-0 bg-black/40 flex items-center justify-center">
            <div className="bg-[var(--surface)] p-6 rounded-xl w-full max-w-md space-y-4">
              <M3FloatingInput
                label="Name"
                value={name}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setName(e.target.value)
                }
              />
              <div>
                <label className="text-[var(--primary)] font-semibold text-sm mb-1 block">
                  World
                </label>
                <select
                  value={worldId}
                  onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                    setWorldId(e.target.value)
                  }
                  className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--surface)]"
                >
                  <option value="">Select world</option>
                  {worlds.map((w: { id: number; name: string }) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-[var(--primary)] font-semibold text-sm mb-1 block">
                  Logo
                </label>
                <input
                  type="file"
                  accept="image/*"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setLogo(e.target.files?.[0] || null)
                  }
                  className="w-full"
                />
              </div>
              <PageRefSelectorMD3
                options={users.map(
                  (u: {
                    id: number;
                    nickname: string;
                    image_url?: string | null;
                  }) => ({
                    id: u.id,
                    name: u.nickname,
                    logo: u.image_url,
                  }),
                )}
                value={members}
                onChange={setMembers}
                label="Members"
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
      </div>
    </DashboardLayout>
  );
}
