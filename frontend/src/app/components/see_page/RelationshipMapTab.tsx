"use client";
import { useState, useEffect } from "react";
import RelationshipGraph from "./RelationshipGraph";
import RelationshipTable from "./RelationshipTable";
import AddRelationshipModal from "./AddRelationshipModal";

interface Relationship {
  id: number;
  page_id: number;
  target_page_id: number;
  relationship_type: string;
  description?: string;
  source_page_id?: number | null;
  author_type: string;
  author_id: number;
  added_at?: string;
  direction: string;
}

interface PageInfo {
  id: number;
  name: string;
  logo?: string;
}

export default function RelationshipMapTab({ page, pages }: { page: { relationship_map: Relationship[]; id: number }; pages: PageInfo[] }) {
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    setRelationships(page.relationship_map || []);
  }, [page]);

  const handleAdd = (rel: Omit<Relationship, "id" | "page_id" | "author_type" | "author_id" | "added_at">) => {
    const newRel: Relationship = {
      id: Date.now(),
      page_id: page.id,
      author_type: "user",
      author_id: 0,
      added_at: new Date().toISOString(),
      ...rel,
    };
    setRelationships((r) => [...r, newRel]);
  };

  return (
    <div className="space-y-4">
      <RelationshipGraph
        nodes={pages.map((p) => ({ id: p.id, name: p.name, logo: p.logo }))}
        links={relationships.map((r) => ({
          id: r.id,
          source: page.id,
          target: r.target_page_id,
          type: r.relationship_type,
          direction: r.direction,
          description: r.description,
        }))}
      />
      <RelationshipTable relationships={relationships} pages={pages} />
      <button className="px-3 py-1 bg-[var(--primary)] text-white rounded" onClick={() => setShowModal(true)}>
        Add Relationship
      </button>
      {showModal && <AddRelationshipModal pages={pages} onAdd={handleAdd} onClose={() => setShowModal(false)} />}
    </div>
  );
}
