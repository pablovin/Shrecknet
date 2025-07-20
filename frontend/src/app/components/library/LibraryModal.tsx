"use client";
import { useState } from "react";
import ModalContainer from "../template/modalContainer";
import { M3FloatingInput } from "../template/M3FloatingInput";
import { createLibraryItem, updateLibraryItem, deleteLibraryItem } from "../../lib/libraryAPI";
import { useAuth } from "../auth/AuthProvider";

export default function LibraryModal({ item, onClose, onSave, onDelete }) {
  const isEdit = !!item;
  const [form, setForm] = useState({
    name: item?.name || "",
    system: item?.system || "",
    description: item?.description || "",
  });
  const [file, setFile] = useState<File | null>(null);
  const { token } = useAuth();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (isEdit) {
        await updateLibraryItem(item.id, form, token);
      } else {
        const fd = new FormData();
        fd.append("name", form.name);
        fd.append("system", form.system);
        fd.append("description", form.description);
        if (file) fd.append("file", file);
        await createLibraryItem(fd, token);
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
      await deleteLibraryItem(item.id, token);
      onDelete?.();
      onClose();
    } catch (err: any) {
      setError(err?.detail || err?.message || String(err));
    }
    setSaving(false);
  }

  return (
    <ModalContainer title={isEdit ? "Edit Item" : "Add Item"} onClose={onClose}>
      {error && (
        <div className="bg-red-100 text-red-700 rounded-lg px-3 py-2 mb-3 text-sm">
          {error}
        </div>
      )}
      <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
        <M3FloatingInput
          label="Name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          required
        />
        <M3FloatingInput
          label="System"
          value={form.system}
          onChange={(e) => setForm({ ...form, system: e.target.value })}
          required
        />
        <M3FloatingInput
          label="Description"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
        {!isEdit && (
          <input
            type="file"
            required
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
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
