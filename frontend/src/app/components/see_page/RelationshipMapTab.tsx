"use client";
import { useState, useEffect } from "react";
import RelationshipGraph from "./RelationshipGraph";
import RelationshipTable from "./RelationshipTable";
import AddRelationshipModal from "./AddRelationshipModal";
import { useAuth } from "../auth/AuthProvider";
import { getPage } from "@/app/lib/pagesAPI";

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

export default function RelationshipMapTab({
  page,
  pages,
}: {
  page: { relationship_map: Relationship[]; id: number };
  pages: PageInfo[];
}) {
  const { token } = useAuth();
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [showModal, setShowModal] = useState(false);
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
      const visited = new Set<number>();

      const addNode = (p: any) => {
        if (!p || visited.has(p.id)) return;
        nodes.push({
          id: p.id.toString(),
          name: p.name,
          logo: p.logo,
          color: colorForConcept(p.concept_id || 0),
          description: p.content,
        });
        visited.add(p.id);
      };

      addNode(pageMap.get(page.id));

      let queue = [
        { id: page.id, rels: page.relationship_map || [] },
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
            links.push({
              id: rel.id.toString(),
              source: src.toString(),
              target: dst.toString(),
              label: rel.relationship_type,
            });
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
  }, [page, pages, token]);

  const handleAdd = (
    rel: Omit<
      Relationship,
      "id" | "page_id" | "author_type" | "author_id" | "added_at"
    >
  ) => {
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
      <RelationshipGraph nodes={graphNodes} links={graphLinks} />
      <RelationshipTable relationships={relationships} pages={pages} />
      <button
        className="px-3 py-1 bg-[var(--primary)] text-white rounded"
        onClick={() => setShowModal(true)}
      >
        Add Relationship
      </button>
      {showModal && (
        <AddRelationshipModal
          pages={pages}
          onAdd={handleAdd}
          onClose={() => setShowModal(false)}
        />
      )}
    </div>
  );
}
