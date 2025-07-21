"use client";
import { useState } from "react";
import ModalContainer from "../template/modalContainer";
import { useLibraryItems } from "../../lib/useLibraryItems";
import { linkAgentItem } from "../../lib/specialistAPI";
import { useAuth } from "../auth/AuthProvider";

export default function AgentItemModal({ agentId, onClose, onSaved }) {
  const { token } = useAuth();
  const { items } = useLibraryItems();
  const [selected, setSelected] = useState<number[]>([]);
  const [systemFilter, setSystemFilter] = useState("");
  const [saving, setSaving] = useState(false);

  const systems = Array.from(new Set((items || []).map(it => it.system)));
  const filtered = (items || []).filter(it => !systemFilter || it.system === systemFilter);

  function toggle(id:number) {
    setSelected(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]);
  }

  async function handleAdd(e:any) {
    e.preventDefault();
    if (selected.length === 0) return;
    setSaving(true);
    try {
      for (const id of selected) {
        await linkAgentItem(agentId, id, token || "");
      }
      onSaved?.();
      onClose();
    } catch (err) {
      console.error(err);
    }
    setSaving(false);
  }

  return (
    <ModalContainer title="Add Library Items" onClose={onClose}>
      <form className="flex flex-col gap-4" onSubmit={handleAdd}>
        <select
          className="border rounded-xl p-2"
          value={systemFilter}
          onChange={e => setSystemFilter(e.target.value)}
        >
          <option value="">All systems</option>
          {systems.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <div className="max-h-80 overflow-y-auto grid sm:grid-cols-2 md:grid-cols-3 gap-3 p-1">
          {filtered.map(it => (
            <label key={it.id} className="border rounded-xl p-3 bg-[var(--surface)] cursor-pointer flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  className="accent-[var(--primary)]"
                  checked={selected.includes(it.id)}
                  onChange={() => toggle(it.id)}
                />
                <span className="font-semibold text-[var(--primary)]">{it.name}</span>
              </div>
              <span className="text-xs text-[var(--muted-foreground)]">{it.system}</span>
              {it.description && (
                <span className="text-xs text-[var(--muted-foreground)] line-clamp-2">{it.description}</span>
              )}
            </label>
          ))}
        </div>
        <div className="flex justify-end gap-2 mt-2">
          <button type="submit" disabled={saving || selected.length === 0} className="px-4 py-2 rounded-xl bg-[var(--primary)] text-white">
            {saving ? "Adding..." : "Add Selected"}
          </button>
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-xl border">Cancel</button>
        </div>
      </form>
    </ModalContainer>
  );
}
