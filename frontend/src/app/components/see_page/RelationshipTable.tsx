"use client";
import Link from "next/link";

interface Relationship {
  id: number;
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

export default function RelationshipTable({
  relationships,
  pages,
  onEdit,
  onDelete,
}: {
  relationships: Relationship[];
  pages: PageInfo[];
  onEdit?: (r: Relationship) => void;
  onDelete?: (id: number) => void;
}) {
  const getPage = (id: number) => pages.find((p) => p.id === id);
  return (
    <table className="w-full text-sm mt-4 border">
      <thead className="bg-[var(--surface)]">
        <tr>
          <th className="px-2 py-1 text-left">Target</th>
          <th className="px-2 py-1 text-left">Type</th>
          <th className="px-2 py-1 text-left">Description</th>
          <th className="px-2 py-1 text-left">Source</th>
          {onEdit && <th></th>}
        </tr>
      </thead>
      <tbody>
        {relationships.map((rel) => {
          const target = getPage(rel.target_page_id);
          const source = rel.source_page_id ? getPage(rel.source_page_id) : null;
          return (
            <tr key={rel.id} className="even:bg-[var(--surface)]/50">
              <td className="px-2 py-1">
                {target ? <Link href={`/pages/${target.id}`}>{target.name}</Link> : rel.target_page_id}
              </td>
              <td className="px-2 py-1">{rel.relationship_type}</td>
              <td className="px-2 py-1">{rel.description}</td>
              <td className="px-2 py-1">
                {source ? <Link href={`/pages/${source.id}`}>{source.name}</Link> : "-"}
              </td>
              {onEdit && (
                <td className="px-2 py-1 space-x-1">
                  <button onClick={() => onEdit(rel)} className="text-xs text-blue-500">Edit</button>
                  <button onClick={() => onDelete?.(rel.id)} className="text-xs text-red-500">Delete</button>
                </td>
              )}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
