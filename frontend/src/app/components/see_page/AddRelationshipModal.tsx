"use client";
import { useState } from "react";
import { Combobox } from "@headlessui/react";
import ModalContainer from "../template/modalContainer";

interface PageInfo {
  id: number;
  name: string;
  logo?: string;
}

interface RelationshipInput {
  target_page_id: number;
  relationship_type: string;
  description?: string;
  source_page_id?: number | null;
  direction: string;
}

export default function AddRelationshipModal({ pages, onAdd, onClose }: { pages: PageInfo[]; onAdd: (r: RelationshipInput) => void; onClose: () => void }) {
  const [target, setTarget] = useState<string>(""
  );
  const [filter, setFilter] = useState("");
  const [type, setType] = useState("friend");
  const [description, setDescription] = useState("");
  const [source, setSource] = useState<string>("");
  const [direction, setDirection] = useState("outgoing");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!target) return;
    onAdd({
      target_page_id: Number(target),
      relationship_type: type,
      description,
      source_page_id: source ? Number(source) : undefined,
      direction,
    });
    onClose();
  };

  return (
    <ModalContainer title="Add Relationship" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="font-semibold text-sm">Target Page</label>
          <Combobox value={target} onChange={setTarget}>
            <Combobox.Input
              className="border w-full rounded px-2 py-1 bg-[var(--surface)]"
              displayValue={() => pages.find((p) => p.id.toString() === target)?.name || ""}
              onChange={(e) => setFilter(e.target.value)}
            />
            <Combobox.Options className="absolute bg-[var(--surface)] border w-full max-h-60 overflow-auto z-20">
              {pages
                .filter((p) => p.name.toLowerCase().includes(filter.toLowerCase()))
                .map((p) => (
                  <Combobox.Option key={p.id} value={p.id.toString()} className="px-2 py-1 cursor-pointer hover:bg-[var(--accent)]/20">
                    {p.name}
                  </Combobox.Option>
                ))}
            </Combobox.Options>
          </Combobox>
        </div>
        <div className="flex flex-col gap-1">
          <label className="font-semibold text-sm">Relationship Type</label>
          <input value={type} onChange={(e) => setType(e.target.value)} className="border rounded px-2 py-1 bg-[var(--surface)]" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="font-semibold text-sm">Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} className="border rounded px-2 py-1 bg-[var(--surface)]" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="font-semibold text-sm">Source Page</label>
          <select value={source} onChange={(e) => setSource(e.target.value)} className="border rounded px-2 py-1 bg-[var(--surface)]">
            <option value="">None</option>
            {pages.map((p) => (
              <option key={p.id} value={p.id.toString()}>{p.name}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="font-semibold text-sm">Direction</label>
          <select value={direction} onChange={(e) => setDirection(e.target.value)} className="border rounded px-2 py-1 bg-[var(--surface)]">
            <option value="outgoing">From this page</option>
            <option value="incoming">To this page</option>
          </select>
        </div>
        <button type="submit" className="px-3 py-1 bg-[var(--primary)] text-white rounded">
          Add
        </button>
      </form>
    </ModalContainer>
  );
}
