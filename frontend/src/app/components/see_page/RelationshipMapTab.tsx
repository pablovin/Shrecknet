"use client";
import { useState, useEffect } from "react";
import RelationshipGraph from "./RelationshipGraph";
import RelationshipTable from "./RelationshipTable";
import AddRelationshipModal, { RelationshipInput } from "./AddRelationshipModal";
import { useAuth } from "../auth/AuthProvider";
import { getPage, createRelationship, updateRelationship, deleteRelationship } from "@/app/lib/pagesAPI";

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
  gameworld_id?: number;
  concept_id?: number;
}

export default function RelationshipMapTab({
  page,
  pages,
}: {
  page: { relationship_map: Relationship[]; id: number; gameworld_id: number; concept_id: number };
  pages: PageInfo[];
}) {
  const { token } = useAuth();
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<Relationship | null>(null);
  const [graphNodes, setGraphNodes] = useState<any[]>([]);
  const [graphLinks, setGraphLinks] = useState<any[]>([]);

  useEffect(() => {
    setRelationships(page.relationship_map || []);
  }, [page]);

  useEffect(() => {
    async function buildGraph() {
      const pageMap = new Map<number, any>();
      pages.forEach((p) => pageMap.set(p.id, p));
      if (!pageMap.has(page.id)) pageMap.set(page.id, page);

      const palette = [
        "#BB86FC",
        "#03DAC6",
        "#F9A825",
        "#FF7043",
        "#4DB6AC",
        "#7986CB",
        "#81C784",
        "#9575CD",
        "#F06292",
      ];
      const colorForConcept = (id: number) =>
        palette[id % palette.length];

      const nodes: any[] = [];
      const links: any[] = [];
      const addedPairs = new Set<string>();
      const visited = new Set<number>();

      const addNode = (p: any) => {
        if (!p || visited.has(p.id)) return;
        nodes.push({
          id: p.id.toString(),
          name: p.name,
          logo: p.logo,
          color: colorForConcept(p.concept_id || 0),
          description: p.content,
          world_id: p.gameworld_id,
          concept_id: p.concept_id,
        });
        visited.add(p.id);
      };

      addNode(pageMap.get(page.id));

      let queue = [
        { id: page.id, rels: relationships },
      ];
      let depth = 0;
      const MAX_DEPTH = 2;

      async function fetchPage(id: number) {
        if (pageMap.has(id)) return pageMap.get(id);
        if (!token) return null;
        try {
          const p = await getPage(id, token);
          pageMap.set(p.id, p);
          return p;
        } catch {
          return null;
        }
      }

      while (queue.length && depth < MAX_DEPTH) {
        const next: any[] = [];
        for (const q of queue) {
          const rels = q.rels || [];
          const slice = rels.slice(0, 5);
          if (rels.length > 5) {
            const n = nodes.find((n) => n.id === q.id.toString());
            if (n) n.moreCount = rels.length - 5;
          }
          for (const rel of slice) {
            const src =
              rel.direction === "incoming" ? rel.target_page_id : q.id;
            const dst =
              rel.direction === "incoming" ? q.id : rel.target_page_id;
            const key = `${Math.min(src, dst)}:${Math.max(src, dst)}:${rel.relationship_type}`;
            if (!addedPairs.has(key)) {
              links.push({
                id: rel.id.toString(),
                source: src.toString(),
                target: dst.toString(),
                label: rel.relationship_type,
              });
              addedPairs.add(key);
            }
            const p = await fetchPage(rel.target_page_id);
            addNode(p);
            next.push({ id: rel.target_page_id, rels: p?.relationship_map });
          }
        }
        queue = next;
        depth++;
      }

      setGraphNodes(nodes);
      setGraphLinks(links);
    }

    buildGraph();
  }, [page.id, pages, token, relationships]);

  const handleAdd = async (rel: RelationshipInput) => {
    if (!token) return;
    try {
      const newRel: Relationship = await createRelationship(
        page.id,
        { ...rel, page_id: page.id, author_type: "user", author_id: 0 },
        token
      );
      setRelationships((r) => [...r, newRel]);

      // Create reciprocal relationship on target page
      const reverseDir = rel.direction === "incoming" ? "outgoing" : "incoming";
      await createRelationship(
        rel.target_page_id,
        {
          page_id: rel.target_page_id,
          target_page_id: page.id,
          relationship_type: rel.relationship_type,
          description: rel.description,
          source_page_id: rel.source_page_id,
          direction: reverseDir,
          author_type: "user",
          author_id: 0,
        },
        token
      );
    } catch (err) {
      console.error("Failed to create relationship", err);
    }
  };

  const handleEdit = async (id: number, rel: RelationshipInput) => {
    if (!token) return;
    try {
      const updated: Relationship = await updateRelationship(id, rel, token);
      setRelationships((r) => r.map((p) => (p.id === id ? updated : p)));
    } catch (err) {
      console.error("Failed to update relationship", err);
    }
  };

  const handleDelete = async (id: number) => {
    if (!token) return;
    try {
      await deleteRelationship(id, token);
      setRelationships((r) => r.filter((rel) => rel.id !== id));
    } catch (err) {
      console.error("Failed to delete relationship", err);
    }
  };

  return (
    <div className="space-y-4">
      <RelationshipGraph nodes={graphNodes} links={graphLinks} />
      <RelationshipTable
        relationships={relationships}
        pages={pages}
        onEdit={(rel) => {
          setEditing(rel);
          setShowModal(true);
        }}
        onDelete={handleDelete}
      />
      <button
        className="px-3 py-1 bg-[var(--primary)] text-white rounded"
        onClick={() => {
          setEditing(null);
          setShowModal(true);
        }}
      >
        Add Relationship
      </button>
      {showModal && (
        <AddRelationshipModal
          pages={pages}
          onSubmit={(data) => {
            if (editing) {
              handleEdit(editing.id, data);
            } else {
              handleAdd(data);
            }
          }}
          onClose={() => {
            setShowModal(false);
            setEditing(null);
          }}
          initial={editing || undefined}
        />
      )}
    </div>
  );
}
