"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import AuthGuard from "@/app/components/auth/AuthGuard";
import { createUserNote } from "@/app/lib/userNotesAPI";
import EditableContent from "@/app/components/editor/EditableContent";
import { M3FloatingInput } from "@/app/components/template/M3FloatingInput";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { getPages } from "@/app/lib/pagesAPI";
import { autoLinkWikiContent } from "@/app/components/editor/autoLinkWikiContent";
import { useUsers } from "@/app/lib/useUsers";
import UserShareSelect from "@/app/components/notes/UserShareSelect";

export default function NewNotePage() {
  const router = useRouter();
  const { token, user } = useAuth();
  const { users } = useUsers();
  const [form, setForm] = useState({ title: "", content: "" });
  const [sharedIds, setSharedIds] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSave(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const pages = await getPages(token || "");
      const linkedContent = autoLinkWikiContent(
        form.content,
        pages,
        null,
        true,
      );
      const payload: {
        title: string;
        content: string;
        shared_with_user_ids?: number[];
      } = {
        title: form.title,
        content: linkedContent,
      };
      if (sharedIds.length > 0) {
        payload.shared_with_user_ids = sharedIds;
      }
      const newNote = await createUserNote(payload, token || "");
      router.push(`/user_notes/${newNote.id}`);
    } catch (err) {
      const e = err as { detail?: string; message?: string };
      setError(e?.detail || e?.message || String(err));
    }
    setSaving(false);
  }

  return (
    <AuthGuard>
      <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-2 sm:px-6 py-8">
        <div className="w-full flex flex-col gap-4">
          <button
            className="self-start px-4 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition"
            onClick={() => router.push("/user_notes")}
          >
            Back to Notes
          </button>
          <form className="flex flex-col gap-4" onSubmit={handleSave}>
            {error && (
              <div className="bg-red-100 text-red-700 rounded-lg px-3 py-2 text-sm">
                {error}
              </div>
            )}
            <M3FloatingInput
              label="Title"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              required
            />
            <UserShareSelect
              users={users}
              selectedIds={sharedIds}
              onChange={setSharedIds}
              currentUserId={user?.id}
            />
            <EditableContent
              content={form.content}
              canEdit
              onSaveContent={(html) => setForm({ ...form, content: html })}
              pageType={`users/${user?.id}`}
              pageName="temp"
              className="max-h-[400px] overflow-y-auto"
            />
            <div className="flex flex-row-reverse gap-3">
              <button
                type="submit"
                className="px-7 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md hover:bg-[var(--accent)] hover:text-[var(--primary)] border border-[var(--primary)]/30 transition-all"
                disabled={saving}
              >
                {saving ? "Saving..." : "Save"}
              </button>
              <button
                type="button"
                onClick={() => router.push("/user_notes")}
                className="px-6 py-2 rounded-xl font-semibold bg-transparent border border-[var(--border)] text-[var(--primary)] hover:bg-[var(--surface-variant)] transition-all"
                disabled={saving}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      </div>
    </AuthGuard>
  );
}
