"use client";
import { useState, ChangeEvent } from "react";
import Image from "next/image";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useTables } from "@/app/lib/useTables";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { createTable, updateTable, deleteTable } from "@/app/lib/tableAPI";
import { uploadTableLogo } from "@/app/lib/uploadTableLogo";
import PageRefSelectorMD3 from "@/app/components/create_page/PageRefSelectorMD3";
import { useWorlds } from "@/app/lib/userWorlds";
import { useUsers } from "@/app/lib/useUsers";
import { M3FloatingInput } from "../components/template/M3FloatingInput";
import Link from "next/link";
import { Users2, Book, Calendar, ArrowRight, Edit, Trash2 } from "lucide-react";
import { ConfirmModal } from "@/app/components/template/ConfirmModal";
import { useTranslation } from "@/app/hooks/useTranslation";

interface TableMember {
  id: number;
  nickname: string;
  image_url?: string | null;
}

interface TableItem {
  id: number;
  world_id: number;
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
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<TableItem | null>(null);
  const [name, setName] = useState("");
  const [worldId, setWorldId] = useState("");
  const [logo, setLogo] = useState<File | null>(null);
  const [members, setMembers] = useState<string[]>([]);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  async function handleSave() {
    if (editing) {
      await updateTable(
        editing.id,
        {
          world_id: Number(worldId),
          name,
          member_ids: members.map((m) => Number(m)),
        },
        token,
      );
      if (logo) {
        const url = await uploadTableLogo(logo, editing.id);
        await updateTable(editing.id, { crest_url: url }, token);
      }
    } else {
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
    }
    setOpen(false);
    setEditing(null);
    setName("");
    setWorldId("");
    setLogo(null);
    setMembers([]);
    mutate();
  }

  function startEdit(t: TableItem) {
    setEditing(t);
    setName(t.name);
    setWorldId(String(t.world_id));
    setLogo(null);
    setMembers(t.members.map((m) => String(m.id)));
    setOpen(true);
  }

  async function handleDelete() {
    if (deleteId === null) return;
    setDeleting(true);
    await deleteTable(deleteId, token);
    setDeleting(false);
    setDeleteId(null);
    mutate();
  }

  return (
    <DashboardLayout>
      <div className="w-full max-w-5xl mx-auto px-2 py-10 min-h-screen relative">
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-3xl font-extrabold font-serif text-[var(--primary)] tracking-wider flex items-center gap-2">
            <Users2 className="w-7 h-7 text-[var(--primary)]" />
            Adventuring Parties
          </h1>
          <button
            className="px-5 py-2.5 rounded-xl bg-[var(--primary)] text-white font-bold shadow-lg border-2 border-[var(--primary)] hover:bg-[var(--primary-dark)] transition"
            onClick={() => {
              setEditing(null);
              setName("");
              setWorldId("");
              setLogo(null);
              setMembers([]);
              setOpen(true);
            }}
          >
            + New Party
          </button>
        </div>

        <ul className="grid gap-7 sm:grid-cols-1">
          {tables.map((t: TableItem) => (
            <li
              key={t.id}
              className="relative flex flex-col min-h-[154px] rounded-3xl border border-[var(--primary)]/15 bg-white shadow-lg overflow-hidden group hover:shadow-2xl hover:border-[var(--primary-dark)] transition"
            >
              <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition">
                <button
                  onClick={() => startEdit(t)}
                  className="p-1 rounded-full bg-white/80 hover:bg-white"
                >
                  <Edit className="w-4 h-4 text-[var(--primary)]" />
                </button>
                <button
                  onClick={() => setDeleteId(t.id)}
                  className="p-1 rounded-full bg-white/80 hover:bg-white"
                >
                  <Trash2 className="w-4 h-4 text-[var(--primary)]" />
                </button>
              </div>
              {/* Top Banner Row */}
              <div className="flex items-center gap-4 px-5 pt-5 pb-2">
                <Image
                  src={t.crest_url || "/images/worlds/new_game.png"}
                  alt={t.name}
                  width={72}
                  height={72}
                  className="w-16 h-16 rounded-xl object-cover border-1 border-[var(--primary)] bg-white shadow-md"
                />
                <div className="flex-1 min-w-0">
                  <Link
                    href={`/tables/${t.id}/sessions`}
                    className="block text-xl font-serif font-bold truncate text-[var(--primary)] hover:underline transition"
                  >
                    {t.name}
                  </Link>
                  <span className="inline-flex items-center gap-1 text-xs font-semibold bg-[var(--primary)]/10 text-[var(--primary)] rounded px-2 py-0.5 mt-1 mr-2">
                    <Book className="w-4 h-4 inline" /> {t.world_name}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <ArrowRight className="w-5 h-5 text-[var(--primary)]" />
                  <Link
                    href={`/tables/${t.id}/sessions`}
                    className="text-xs font-semibold text-[var(--primary)] underline"
                  >
                    Sessions
                  </Link>
                </div>
              </div>
              {/* Members Row */}
              <div className="flex flex-wrap items-center gap-3 px-5 pb-2">
                {t.members.map((m: TableMember) => (
                  <div
                    key={m.id}
                    className="flex items-center gap-2 bg-[var(--primary)]/5 rounded-full px-2 py-1 shadow text-xs"
                  >
                    <Image
                      src={m.image_url || "/images/avatars/default.png"}
                      alt={m.nickname}
                      width={28}
                      height={28}
                      className="w-7 h-7 rounded-full object-cover border-2 border-[var(--primary)]"
                    />
                    <span className="font-semibold text-[var(--primary-dark)]">
                      {m.nickname}
                    </span>
                  </div>
                ))}
              </div>
              {/* Details Bar */}
              <div className="flex flex-wrap justify-between items-center px-5 pb-3 pt-2 border-t border-[var(--primary)] bg-[var(--primary)]/5 mt-3">
                <div className="flex items-center gap-2 text-xs text-[var(--primary-dark)]">
                  <Calendar className="w-4 h-4" />
                  {t.latest_session ? (
                    <span>
                      Last:{" "}
                      {new Date(t.latest_session).toLocaleDateString(
                        undefined,
                        {
                          timeZone: "UTC",
                        },
                      )}{" "}
                      (
                      {Math.floor(
                        (Date.now() - new Date(t.latest_session).getTime()) /
                          (1000 * 60 * 60 * 24),
                      )}{" "}
                      days ago)
                    </span>
                  ) : (
                    <span>No sessions yet</span>
                  )}
                </div>
                {t.next_session && (
                  <div className="flex items-center gap-2 text-xs text-[var(--primary)] font-semibold">
                    <Calendar className="w-4 h-4" />
                    Next:{" "}
                    {new Date(t.next_session).toLocaleDateString(undefined, {
                      timeZone: "UTC",
                    })}{" "}
                    (in{" "}
                    {Math.ceil(
                      (new Date(t.next_session).getTime() - Date.now()) /
                        (1000 * 60 * 60 * 24),
                    )}{" "}
                    days)
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>

        {/* Modal */}
        {open && (
          <div className="fixed inset-0 z-40 bg-black/40 flex items-center justify-center">
            <div className="bg-[var(--surface)] border-2 border-[var(--primary)] shadow-xl rounded-2xl p-8 w-full max-w-md relative">
              <h2 className="text-xl font-serif font-bold text-[var(--primary)] mb-2 text-center">
                {editing ? "Edit Party" : "Create a New Party"}
              </h2>
              <M3FloatingInput
                label="Party Name"
                value={name}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setName(e.target.value)
                }
              />
              <div className="mt-3">
                <label className="text-[var(--primary)] font-semibold text-sm mb-1 block">
                  World
                </label>
                <select
                  value={worldId}
                  onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                    setWorldId(e.target.value)
                  }
                  className="w-full px-3 py-2 rounded-lg border border-[var(--primary)] bg-[var(--surface)] text-[var(--primary-dark)]"
                >
                  <option value="">Select world</option>
                  {worlds.map((w: { id: number; name: string }) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="mt-3">
                <label className="text-[var(--primary)] font-semibold text-sm mb-1 block">
                  Party Crest (Logo)
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
              <div className="mt-3">
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
                  label="Party Members"
                />
              </div>
              <div className="flex justify-end gap-2 mt-5">
                <button
                  onClick={() => setOpen(false)}
                  className="px-4 py-2 rounded-lg font-semibold text-[var(--primary)] hover:bg-[var(--primary)]/10 transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  className="px-4 py-2 rounded-lg bg-[var(--primary)] text-white font-bold shadow hover:bg-[var(--primary-dark)] transition"
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
      <ConfirmModal
        open={deleteId !== null}
        title={t("delete_table")}
        message={t("confirm_delete_table")}
        onConfirm={handleDelete}
        onCancel={() => setDeleteId(null)}
        loading={deleting}
      />
    </DashboardLayout>
  );
}
