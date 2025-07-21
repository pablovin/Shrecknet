"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import { useLibraryItems } from "../lib/useLibraryItems";
import { useState } from "react";
import { startLibraryVectorJob } from "../lib/libraryAPI";
import { useLibraryJobs } from "../lib/useLibraryJobs";
import LibraryGrid from "../components/library/LibraryGrid";
import LibraryModal from "../components/library/LibraryModal";
import { PlusCircle } from "lucide-react";


export default function LibraryPage() {
  const { user, token } = useAuth();
  const { items, isLoading, mutate } = useLibraryItems();
  const { jobs, mutate: refreshJobs } = useLibraryJobs();
  const [modalOpen, setModalOpen] = useState(false);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState("");
  const [systemFilter, setSystemFilter] = useState("");
  const [embeddingId, setEmbeddingId] = useState<number | null>(null);
  const [toastMsg, setToastMsg] = useState("");

  const allowed = useRoleRedirect("system admin");
  if (!allowed) return null;

  function handleEdit(item) {
    setSelected(item);
    setModalOpen(true);
  }

  function handleNew() {
    setSelected(null);
    setModalOpen(true);
  }

  function handleSaved() {
    mutate();
  }

  const systems = Array.from(new Set((items || []).map((it) => it.system)));

  const filteredItems = (items || []).filter(
    (it) =>
      (!systemFilter || it.system === systemFilter) &&
      (it.name.toLowerCase().includes(search.toLowerCase()) ||
        it.description?.toLowerCase().includes(search.toLowerCase()))
  );

  const jobsByItem = jobs.reduce<Record<number, any[]>>((acc, j) => {
    if (!acc[j.item_id]) acc[j.item_id] = [];
    acc[j.item_id].push(j);
    return acc;
  }, {} as Record<number, any[]>);

  async function handleEmbed(item) {
    if (jobsByItem[item.id]?.some((j) => j.status !== "done" && j.status !== "queued")) {
      setToastMsg("A job is already running for this item.");
      setTimeout(() => setToastMsg(""), 2000);
      return;
    }
    setEmbeddingId(item.id);
    try {
      await startLibraryVectorJob(item.id, token || "");
      refreshJobs();
      setToastMsg("Embedding started. The job will run in the background.");
      setTimeout(() => setToastMsg(""), 2000);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error(err);
    }
    setEmbeddingId(null);
  }

  async function handleEmbedAll() {
    for (const it of filteredItems) {
      await handleEmbed(it);
    }
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        {toastMsg && (
          <div className="fixed top-8 left-1/2 transform -translate-x-1/2 bg-[var(--primary)] text-[var(--primary-foreground)] px-4 py-2 rounded-xl shadow z-[1000] text-sm animate-fade-in-out">
            {toastMsg}
          </div>
        )}
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] transition-colors duration-300 px-2 sm:px-6 py-8">
          <div className="mx-auto max-w-5xl w-full flex flex-col gap-8">
            <div className="flex items-center justify-between mb-3">
              <h1 className="text-2xl font-serif font-bold text-[var(--primary)] tracking-tight">
                Library Admin
              </h1>
              <div className="flex gap-2">
                <button
                  className="flex items-center gap-2 px-4 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition"
                  onClick={handleNew}
                >
                  <PlusCircle className="w-5 h-5" /> Add
                </button>
                <button
                  className="flex items-center gap-2 px-4 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition"
                  onClick={handleEmbedAll}
                >
                  Embed All
                </button>
              </div>
            </div>

            {/* Filters */}
            <div className="flex flex-wrap gap-4 mb-4 items-center justify-center bg-[var(--surface-variant)]/60 rounded-xl p-3">
              <input
                className="px-4 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] placeholder-[var(--primary)]/60 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] text-base shadow transition"
                placeholder="Search..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <select
                className="px-3 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] text-base shadow transition"
                value={systemFilter}
                onChange={(e) => setSystemFilter(e.target.value)}
              >
                <option value="">All systems</option>
                {systems.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            {isLoading ? (
              <div>Loading...</div>
            ) : (
              <LibraryGrid
                items={filteredItems}
                onItemClick={handleEdit}
                onEmbed={handleEmbed}
                jobsByItem={jobsByItem}
                embeddingId={embeddingId}
              />
            )}
          </div>
          {modalOpen && (
            <LibraryModal
              item={selected}
              onClose={() => setModalOpen(false)}
              onSave={handleSaved}
              onDelete={handleSaved}
            />
          )}
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
