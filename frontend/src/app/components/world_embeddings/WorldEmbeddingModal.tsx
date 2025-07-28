"use client";
import { useState } from "react";
import ModalContainer from "../template/modalContainer";
import { M3FloatingInput } from "../template/M3FloatingInput";
import { useAuth } from "../auth/AuthProvider";
import { updateWorldEmbedding } from "../../lib/worldEmbeddingAPI";

export default function WorldEmbeddingModal({ embedding, worlds, onClose, onSaved }) {
  const [form, setForm] = useState({
    world_id: embedding.world_id,
    name: embedding.name,
    collection: embedding.collection,
  });
  const { token } = useAuth();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: any) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      await updateWorldEmbedding(embedding.id, form, token || "");
      onSaved?.();
      onClose();
    } catch (err: any) {
      setError(err?.detail || err?.message || String(err));
    }
    setSaving(false);
  }

  return (
    <ModalContainer title="Edit Embedding" onClose={onClose}>
      {error && (
        <div className="bg-red-100 text-red-700 rounded-lg px-3 py-2 mb-3 text-sm">{error}</div>
      )}
      <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
        <select
          value={form.world_id}
          onChange={e => setForm({ ...form, world_id: Number(e.target.value) })}
          className="border px-3 py-2 rounded-xl"
        >
          {worlds.map((w:any) => (
            <option key={w.id} value={w.id}>{w.name}</option>
          ))}
        </select>
        <M3FloatingInput
          label="Name"
          value={form.name}
          onChange={e => setForm({ ...form, name: e.target.value })}
          required
        />
        <M3FloatingInput
          label="Collection"
          value={form.collection}
          onChange={e => setForm({ ...form, collection: e.target.value })}
          required
        />
        <div className="flex flex-row-reverse gap-3 mt-3">
          <button
            type="submit"
            className="px-7 py-2 rounded-xl font-bold bg-purple-600 text-white shadow-md"
            disabled={saving}
          >
            {saving ? "Saving..." : "Save"}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="px-6 py-2 rounded-xl font-semibold border"
          >
            Cancel
          </button>
        </div>
      </form>
    </ModalContainer>
  );
}
