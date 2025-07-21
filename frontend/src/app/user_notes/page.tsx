"use client";
import { useState } from "react";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useUserNotes } from "../lib/useUserNotes";
import NoteModal from "../components/notes/NoteModal";
import NoteList from "../components/notes/NoteList";
import { PlusCircle } from "lucide-react";

export default function UserNotesPage() {
  const { notes, mutate, isLoading } = useUserNotes();
  const [modalOpen, setModalOpen] = useState(false);
  const [selected, setSelected] = useState(null);

  function handleEdit(note) {
    setSelected(note);
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
          <div className="mx-auto max-w-5xl w-full flex flex-col gap-6">
            <div className="flex items-center justify-between mb-4">
              <h1 className="text-2xl font-serif font-bold text-[var(--primary)] tracking-tight">My Notes</h1>
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
              <NoteList notes={notes} onEdit={handleEdit} />
            )}
          </div>
          {modalOpen && (
            <NoteModal note={selected} onClose={() => setModalOpen(false)} onSave={handleSaved} />
          )}
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
