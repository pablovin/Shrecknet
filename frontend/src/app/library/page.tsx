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

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] transition-colors duration-300 px-2 sm:px-6 py-8">
          <div className="mx-auto max-w-5xl w-full flex flex-col gap-8">
            <div className="flex items-center justify-between mb-3">
              <h1 className="text-2xl font-serif font-bold text-[var(--primary)] tracking-tight">
                Library
              </h1>
              <button
                className="flex items-center gap-2 px-4 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition"
                onClick={handleNew}
              >
                <PlusCircle className="w-5 h-5" /> Add
              </button>
            </div>
            {isLoading ? (
              <div>Loading...</div>
            ) : (
              <LibraryGrid items={items} onItemClick={handleEdit} />
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
