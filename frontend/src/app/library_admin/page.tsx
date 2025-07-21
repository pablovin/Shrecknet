"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { hasRole } from "../lib/roles";
import { useAuth } from "../components/auth/AuthProvider";
import { useLibraryItems } from "../lib/useLibraryItems";
import { useState } from "react";
import LibraryGrid from "../components/library/LibraryGrid";
import LibraryModal from "../components/library/LibraryModal";
import { PlusCircle } from "lucide-react";


export default function LibraryPage() {
  const { user } = useAuth();
  const { items, isLoading, mutate } = useLibraryItems();
  const [modalOpen, setModalOpen] = useState(false);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState("");
  const [systemFilter, setSystemFilter] = useState("");

  if (!hasRole(user?.role, "system admin")) {
    return (
      <DashboardLayout>
        <div className="p-10 text-2xl text-red-600 font-bold">Not authorized</div>
      </DashboardLayout>
    );
  }

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

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] transition-colors duration-300 px-2 sm:px-6 py-8">
          <div className="mx-auto max-w-5xl w-full flex flex-col gap-8">
            <div className="flex items-center justify-between mb-3">
              <h1 className="text-2xl font-serif font-bold text-[var(--primary)] tracking-tight">
                Library Admin
              </h1>
              <button
                className="flex items-center gap-2 px-4 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition"
                onClick={handleNew}
              >
                <PlusCircle className="w-5 h-5" /> Add
              </button>
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
              <LibraryGrid items={filteredItems} onItemClick={handleEdit} />
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
