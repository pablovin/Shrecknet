"use client";
import { useState } from "react";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useUserNotes } from "../lib/useUserNotes";
import NoteModal from "../components/notes/NoteModal";
import NoteList from "../components/notes/NoteList";
import { PlusCircle } from "lucide-react";
import { useAuth } from "../components/auth/AuthProvider";

export default function UserNotesPage() {
  const { notes, mutate, isLoading } = useUserNotes();
  const { user } = useAuth();
  const [modalOpen, setModalOpen] = useState(false);

  function handleNew() {
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
              <>
                <h2 className="text-xl font-bold mt-2">My Notes</h2>
                <NoteList
                  notes={notes.filter((n) => n.user_id === user?.id)}
                />
                <h2 className="text-xl font-bold mt-6">Shared With Me</h2>
                <NoteList
                  notes={notes.filter(
                    (n) => n.user_id !== user?.id && n.shared_with_user_ids?.includes(user?.id)
                  )}
                />
              </>
            )}
          </div>
          {modalOpen && (
            <NoteModal note={null} onClose={() => setModalOpen(false)} onSave={handleSaved} />
          )}
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
