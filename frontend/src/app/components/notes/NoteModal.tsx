"use client";
import { useState } from "react";
import ModalContainer from "../template/modalContainer";
import { M3FloatingInput } from "../template/M3FloatingInput";
import { useAuth } from "../auth/AuthProvider";
import { createUserNote, updateUserNote, deleteUserNote } from "../../lib/userNotesAPI";
import { useUsers } from "../../lib/useUsers";
import EditableContent from "../editor/EditableContent";
import { getPages } from "../../lib/pagesAPI";
import { autoLinkWikiContent } from "../editor/autoLinkWikiContent";

export default function NoteModal({ note, onClose, onSave }) {
  const isEdit = !!note;
  const [form, setForm] = useState({
    title: note?.title || "",
    content: note?.content || "",
  });
  const { token, user } = useAuth();
  const { users } = useUsers();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const pages = await getPages(token || "");
      const linkedContent = autoLinkWikiContent(form.content, pages, null, true);
      const payload = { ...form, content: linkedContent };
      if (isEdit) {
        await updateUserNote(note.id, payload, token || "");
      } else {
        await createUserNote(payload, token || "");
      }
      onSave?.();
      onClose();
    } catch (err: any) {
      setError(err?.detail || err?.message || String(err));
    }
    setSaving(false);
  }

  async function handleDelete() {
    if (!isEdit) return;
    setSaving(true);
    try {
      await deleteUserNote(note.id, token || "");
      onSave?.();
      onClose();
    } catch (err: any) {
      setError(err?.detail || err?.message || String(err));
    }
    setSaving(false);
  }

  const creator = users.find((u) => u.id === note?.user_id);
  const sharedUsers = users.filter((u) => note?.shared_with_user_ids?.includes(u.id));
  const contributors = (note?.contributors || []).map((c) => ({
    ...c,
    user: users.find((u) => u.id === c.user_id),
  }));

  return (
    <ModalContainer className="w-[90vw] max-w-[90vw]" title={isEdit ? "Edit Note" : "New Note"} onClose={onClose}>
      {error && <div className="bg-red-100 text-red-700 rounded-lg px-3 py-2 mb-3 text-sm">{error}</div>}
      <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
        <M3FloatingInput
          label="Title"
          value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })}
          required
        />
        <EditableContent
          content={form.content}
          canEdit
          onSaveContent={(html) => setForm({ ...form, content: html })}
          pageType={`users/${user?.id}`}
          pageName={note?.id ? `${note.id}` : "temp"}
          className="max-h-[400px] overflow-y-auto"
        />
        {isEdit && (
          <div className="text-sm text-[var(--foreground)]/70 flex flex-col gap-1">
            <div>Creator: {creator?.nickname || note.user_id}</div>
            {sharedUsers.length > 0 && (
              <div>Shared with: {sharedUsers.map(u => u.nickname).join(', ')}</div>
            )}
            {contributors.length > 0 && (
              <div>
                Contributors:
                {contributors.map(c => ` ${c.user?.nickname || c.user_id} (${new Date(c.date).toLocaleDateString()})`).join(', ')}
              </div>
            )}
          </div>
        )}
        <div className="flex flex-row-reverse gap-3 mt-3">
          <button
            type="submit"
            className="px-7 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md hover:bg-[var(--accent)] hover:text-[var(--primary)] border border-[var(--primary)]/30 transition-all"
            disabled={saving}
          >
            {saving ? "Saving..." : "Save"}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="px-6 py-2 rounded-xl font-semibold bg-transparent border border-[var(--border)] text-[var(--primary)] hover:bg-[var(--surface-variant)] transition-all"
          >
            Cancel
          </button>
          {isEdit && (
            <button
              type="button"
              onClick={handleDelete}
              disabled={saving}
              className="px-6 py-2 rounded-xl font-semibold bg-red-600 text-white hover:bg-red-700 transition-all"
            >
              Delete
            </button>
          )}
        </div>
      </form>
    </ModalContainer>
  );
}
