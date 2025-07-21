"use client";
import { format } from "date-fns";

export default function NoteList({ notes, onEdit }) {
  if (!notes?.length) return <div className="text-center py-10 opacity-60">No notes.</div>;
  return (
    <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
      {notes.map((n) => (
        <button
          key={n.id}
          className="flex flex-col items-start gap-1 p-4 rounded-xl border border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-variant)] text-left"
          onClick={() => onEdit(n)}
        >
          <span className="font-bold text-[var(--primary)]">{n.title}</span>
          <span className="text-xs text-[var(--foreground)]/70">
            {n.note_date ? format(new Date(n.note_date), "PPpp") : ""}
          </span>
        </button>
      ))}
    </div>
  );
}
