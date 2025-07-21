"use client";
import { useState } from "react";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import { useWorlds } from "../lib/userWorlds";
import { useConcepts } from "../lib/useConcept";
import { usePages } from "../lib/usePage";
import { useAgents } from "../lib/useAgents";
import { deletePage } from "../lib/pagesAPI";
import Image from "next/image";
import Link from "next/link";

export default function AllPagesPage() {
  const { user, token } = useAuth();
  const { worlds } = useWorlds();
  const [selectedWorld, setSelectedWorld] = useState<number | "">("");
  const { concepts } = useConcepts(selectedWorld || undefined);
  const { agents } = useAgents(selectedWorld || undefined);
  const [selectedConcept, setSelectedConcept] = useState<number | "">("");
  const { pages, mutate, isLoading } = usePages({
    ...(selectedWorld ? { gameworld_id: selectedWorld } : {}),
    ...(selectedConcept ? { concept_id: selectedConcept } : {}),
  });
  const [search, setSearch] = useState("");
  const [selectedPages, setSelectedPages] = useState<number[]>([]);

  const allowed = useRoleRedirect("writer");
  if (!allowed) return null;

  const worldMap: Record<number, any> = {};
  worlds.forEach((w) => {
    worldMap[w.id] = w;
  });
  const conceptMap: Record<number, any> = {};
  concepts.forEach((c) => {
    conceptMap[c.id] = c;
  });
  const agentMap: Record<number, any> = {};
  agents.forEach((a) => {
    agentMap[a.id] = a;
  });

  const filtered = pages.filter((p) =>
    p.name?.toLowerCase().includes(search.toLowerCase())
  );

  async function handleDeleteSelected() {
    if (!token) return;
    for (const id of selectedPages) {
      try {
        await deletePage(id, token);
      } catch (err) {
        console.error("Failed to delete page", err);
      }
    }
    setSelectedPages([]);
    mutate();
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-2 sm:px-6 py-8">
          <div className="max-w-6xl mx-auto">
            <h1 className="text-xl sm:text-2xl font-serif font-bold text-[var(--primary)] mb-6">All Pages</h1>
            <div className="flex flex-wrap items-end gap-4 mb-6">
              <select
                className="px-3 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-sm"
                value={selectedWorld}
                onChange={(e) => {
                  const val = e.target.value;
                  setSelectedWorld(val ? Number(val) : "");
                  setSelectedConcept("");
                }}
              >
                <option value="">All worlds</option>
                {worlds.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
              <select
                className="px-3 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-sm"
                value={selectedConcept}
                onChange={(e) => {
                  const val = e.target.value;
                  setSelectedConcept(val ? Number(val) : "");
                }}
                disabled={!selectedWorld}
              >
                <option value="">All concepts</option>
                {concepts.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <input
                className="flex-1 min-w-[160px] px-3 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-sm"
                placeholder="Search pages..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <button
                disabled={selectedPages.length === 0}
                onClick={handleDeleteSelected}
                className="px-4 py-2 rounded-xl bg-red-600 text-white disabled:opacity-50"
              >
                Delete Selected
              </button>
            </div>
            <div className="overflow-x-auto rounded-xl border border-[var(--border)] shadow-sm">
              <table className="min-w-full text-sm bg-[var(--card)]">
                <thead>
                  <tr className="text-left text-[var(--primary)]">
                    <th className="w-8"></th>
                    <th className="w-20">Logo</th>
                    <th>Name</th>
                    <th>World</th>
                    <th>Concept</th>
                    <th>Updated</th>
                    <th>Content</th>
                    <th>AI Content</th>
                    <th>Agent</th>
                  </tr>
                </thead>
                <tbody>
                  {isLoading ? (
                    <tr>
                      <td colSpan={9} className="text-center py-4">
                        Loading pages...
                      </td>
                    </tr>
                  ) : filtered.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="text-center py-4">
                        No pages found.
                      </td>
                    </tr>
                  ) : (
                    filtered.map((p) => (
                      <tr
                        key={p.id}
                        className="border-b border-[var(--border)] hover:bg-[var(--surface)]"
                      >
                        <td className="px-2 text-center">
                          <input
                            type="checkbox"
                            checked={selectedPages.includes(p.id)}
                            onChange={(e) => {
                              setSelectedPages((sp) =>
                                e.target.checked
                                  ? [...sp, p.id]
                                  : sp.filter((id) => id !== p.id)
                              );
                            }}
                          />
                        </td>
                        <td className="p-2">
                          <Image
                            src={p.logo || "/images/pages/concept/concept.png"}
                            alt={p.name}
                            width={40}
                            height={40}
                            className="w-10 h-10 rounded object-cover"
                          />
                        </td>
                        <td className="p-2 font-semibold">
                          <Link
                            href={`/worlds/${p.gameworld_id}/concept/${p.concept_id}/page/${p.id}`}
                            className="text-[var(--primary)] hover:underline"
                          >
                            {p.name}
                          </Link>
                        </td>
                        <td className="p-2">
                          {worldMap[p.gameworld_id]?.name || p.gameworld_id}
                        </td>
                        <td className="p-2">
                          {conceptMap[p.concept_id]?.name || p.concept_id}
                        </td>
                        <td className="p-2">
                          {new Date(p.updated_at || p.created_at).toLocaleDateString()}
                        </td>
                        <td className="p-2 text-center">
                          {p.content && p.content.trim() !== "" ? "✅" : "❌"}
                        </td>
                        <td className="p-2 text-center">
                          {p.autogenerated_content && p.autogenerated_content.trim() !== "" ? "✅" : "❌"}
                        </td>
                        <td className="p-2">
                          {p.updated_by_agent_id ? agentMap[p.updated_by_agent_id]?.name || p.updated_by_agent_id : "-"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
