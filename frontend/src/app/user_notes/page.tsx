"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useUserNotes } from "../lib/useUserNotes";
import NoteList from "../components/notes/NoteList";
import { PlusCircle, Notebook, Users } from "lucide-react";
import { useAuth } from "../components/auth/AuthProvider";

export default function UserNotesPage() {
  const { notes, isLoading } = useUserNotes();
  const { user } = useAuth();

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full text-[var(--foreground)] transition-colors duration-300 px-2 sm:px-6 py-10">
          <div className="mx-auto max-w-5xl w-full flex flex-col gap-8">
            <div className="flex items-center justify-between mb-6">
              <h1 className="text-3xl font-serif font-bold text-[var(--primary)] tracking-tight flex items-center gap-3">
                <Notebook className="w-8 h-8 text-[var(--primary)]" />
                My Notes
              </h1>
              <a
                href="/user_notes/new"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 px-5 py-2.5 rounded-xl font-bold bg-[var(--primary)] text-white shadow hover:bg-[var(--primary-dark)] transition"
              >
                <PlusCircle className="w-6 h-6" /> Add Note
              </a>
            </div>

            {isLoading ? (
              <div className="text-center text-lg text-[var(--muted-foreground)]">Loading your notes...</div>
            ) : (
              <div className="w-full flex flex-col md:flex-row gap-8">
                {/* MY NOTES */}
                <div className="flex-1 bg-white/70 rounded-2xl border-0 border-[var(--primary)] shadow-md p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <Notebook className="w-6 h-6 text-[var(--primary)]" />
                    <h2 className="text-xl font-bold text-[var(--primary)] font-serif">My Notes</h2>
                  </div>
                  <NoteList notes={notes.filter((n) => n.user_id === user?.id)} />
                </div>
                {/* SHARED WITH ME */}
                <div className="flex-1 bg-white/70 rounded-2xl border-0 border-[var(--primary)] shadow-md p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <Users className="w-6 h-6 text-[var(--primary)]" />
                    <h2 className="text-xl font-bold text-[var(--primary)] font-serif">Shared With Me</h2>
                  </div>
                  <NoteList
                    notes={notes.filter(
                      (n) => n.user_id !== user?.id && n.shared_with_user_ids?.includes(user?.id)
                    )}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
