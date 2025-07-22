"use client";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import AuthGuard from "../../components/auth/AuthGuard";
import { useUserNote } from "../../lib/useUserNote";
import { useUsers } from "../../lib/useUsers";
import { useAuth } from "../../components/auth/AuthProvider";
import { updateUserNote, deleteUserNote } from "../../lib/userNotesAPI";
import EditableContent from "../../components/editor/EditableContent";
import { M3FloatingInput } from "../../components/template/M3FloatingInput";
import { getPages } from "../../lib/pagesAPI";
import { autoLinkWikiContent } from "../../components/editor/autoLinkWikiContent";

export default function NoteDetailPage() {
  const params = useParams();
  const router = useRouter();
  const noteID = Number(params?.noteID);
  const { note, mutate, isLoading } = useUserNote(noteID);
  const { users } = useUsers();
  const { token, user } = useAuth();

  const [form, setForm] = useState({ title: "", content: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (note) setForm({ title: note.title, content: note.content || "" });
  }, [note]);

  if (isLoading && !note) {
    return (
      <AuthGuard>
        <div className="p-6 text-[var(--foreground)]">Loading...</div>
      </AuthGuard>
    );
  }
  if (!note) {
    return (
      <AuthGuard>
        <div className="p-6 text-[var(--foreground)]">Note not found.</div>
      </AuthGuard>
    );
  }

  const creator = users.find(u => u.id === note.user_id);
  const sharedUsers = users.filter(u => note.shared_with_user_ids?.includes(u.id));
  const contributors = (note.contributors || []).map(c => ({
    ...c,
    user: users.find(u => u.id === c.user_id),
  }));

  async function handleSave(e: any) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const pages = await getPages(token || "");
      const linkedContent = autoLinkWikiContent(form.content, pages, null, true);
      await updateUserNote(noteID, { title: form.title, content: linkedContent }, token || "");
      mutate();
    } catch (err: any) {
      setError(err?.detail || err?.message || String(err));
    }
    setSaving(false);
  }

  async function handleDelete() {
    setSaving(true);
    try {
      await deleteUserNote(noteID, token || "");
      router.push("/user_notes");
    } catch (err: any) {
      setError(err?.detail || err?.message || String(err));
    }
    setSaving(false);
  }

  return (
    <AuthGuard>
      <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-2 sm:px-6 py-8">
        <div className="mx-auto max-w-2xl flex flex-col gap-4">
          <button
            className="self-start px-4 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition"
            onClick={() => router.push('/user_notes')}
          >
            Back to Notes
          </button>
          <form className="flex flex-col gap-4" onSubmit={handleSave}>
            {error && (
              <div className="bg-red-100 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>
            )}
            <M3FloatingInput
              label="Title"
              value={form.title}
              onChange={e => setForm({ ...form, title: e.target.value })}
              required
            />
            <div className="text-sm text-[var(--foreground)]/70 flex flex-col gap-1">
              <div>Creator: {creator?.nickname || note.user_id}</div>
              {sharedUsers.length > 0 && (
                <div>Shared with: {sharedUsers.map(u => u.nickname).join(', ')}</div>
              )}
              {contributors.length > 0 && (
                <div>
                  Contributors:{' '}
                  {contributors.map(c => ` ${c.user?.nickname || c.user_id} (${new Date(c.date).toLocaleDateString()})`).join(', ')}
                </div>
              )}
            </div>
            <EditableContent
              content={form.content}
              canEdit
              onSaveContent={html => setForm({ ...form, content: html })}
              pageType={`users/${user?.id}`}
              pageName={`${note.id}`}
              className="max-h-[400px] overflow-y-auto"
            />
            <div className="flex flex-row-reverse gap-3">
              <button
                type="submit"
                className="px-7 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md hover:bg-[var(--accent)] hover:text-[var(--primary)] border border-[var(--primary)]/30 transition-all"
                disabled={saving}
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
              <button
                type="button"
                onClick={() => router.push('/user_notes')}
                className="px-6 py-2 rounded-xl font-semibold bg-transparent border border-[var(--border)] text-[var(--primary)] hover:bg-[var(--surface-variant)] transition-all"
                disabled={saving}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDelete}
                className="px-6 py-2 rounded-xl font-semibold bg-red-600 text-white hover:bg-red-700 transition-all"
                disabled={saving}
              >
                Delete
              </button>
            </div>
          </form>
        </div>
      </div>
    </AuthGuard>
  );
}
