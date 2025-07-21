"use client";
import { useState } from "react";
import ModalContainer from "../template/modalContainer";
import { useLibraryItems } from "../../lib/useLibraryItems";
import { linkAgentItem } from "../../lib/specialistAPI";
import { useAuth } from "../auth/AuthProvider";

export default function AgentItemModal({ agentId, onClose, onSaved }) {
  const { token } = useAuth();
  const { items } = useLibraryItems();
  const [selected, setSelected] = useState(0);
  const [saving, setSaving] = useState(false);

  async function handleAdd(e:any) {
    e.preventDefault();
    if (!selected) return;
    setSaving(true);
    try {
      await linkAgentItem(agentId, selected, token || "");
      onSaved?.();
      onClose();
    } catch (err) {
      console.error(err);
    }
    setSaving(false);
  }

  return (
    <ModalContainer title="Add Library Item" onClose={onClose}>
      <form className="flex flex-col gap-4" onSubmit={handleAdd}>
        <select
          className="border rounded-xl p-2"
          value={selected}
          onChange={e => setSelected(Number(e.target.value))}
        >
          <option value={0}>Select item</option>
          {items.map(it => (
            <option key={it.id} value={it.id}>{it.name}</option>
          ))}
        </select>
        <div className="flex justify-end gap-2">
          <button type="submit" disabled={saving || !selected} className="px-4 py-2 rounded-xl bg-[var(--primary)] text-white">
            {saving ? "Adding..." : "Add"}
          </button>
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-xl border">Cancel</button>
        </div>
      </form>
    </ModalContainer>
  );
}
