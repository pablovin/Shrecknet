"use client";
import { useState } from "react";
import ModalContainer from "../template/modalContainer";
import { useWorldEmbeddings } from "../../lib/useWorldEmbeddings";
import { setAgentEmbeddings } from "../../lib/worldEmbeddingAPI";
import { useAuth } from "../auth/AuthProvider";

export default function AgentEmbeddingModal({ agentId, worldId, onClose, onSaved }) {
  const { token } = useAuth();
  const { embeddings } = useWorldEmbeddings();
  const [selected, setSelected] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);

  const filtered = embeddings.filter((e:any)=>e.world_id===worldId);

  function toggle(id:number) {
    setSelected(prev=>prev.includes(id)?prev.filter(i=>i!==id):[...prev,id]);
  }

  async function handleSave(e:any){
    e.preventDefault();
    setSaving(true);
    try {
      await setAgentEmbeddings(agentId, selected, token||"" );
      onSaved?.();
      onClose();
    } catch(err){
      console.error(err);
    }
    setSaving(false);
  }

  return (
    <ModalContainer title="Select Embeddings" onClose={onClose}>
      <form className="flex flex-col gap-4" onSubmit={handleSave}>
        <div className="max-h-80 overflow-y-auto grid sm:grid-cols-2 gap-3 p-1">
          {filtered.map(e=> (
            <label key={e.id} className="border rounded-xl p-3 flex gap-2 items-center">
              <input type="checkbox" className="accent-[var(--primary)]" checked={selected.includes(e.id)} onChange={()=>toggle(e.id)} />
              <span>{e.name}</span>
            </label>
          ))}
        </div>
        <div className="flex justify-end gap-2 mt-2">
          <button type="submit" disabled={saving} className="px-4 py-2 rounded-xl bg-[var(--primary)] text-white">
            {saving?"Saving...":"Save"}
          </button>
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-xl border">Cancel</button>
        </div>
      </form>
    </ModalContainer>
  );
}
