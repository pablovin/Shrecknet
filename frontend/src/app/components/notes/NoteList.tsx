"use client";
import { format } from "date-fns";
import { useRouter } from "next/navigation";

export default function NoteList({ notes, users = [], currentUserId }) {
  const router = useRouter();
  if (!notes?.length)
    return <div className="text-center py-10 opacity-60">No notes.</div>;
  return (
    <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
      {notes.map((n) => {
        const owner = users.find((u) => u.id === n.user_id);
        return (
          <button
            key={n.id}
            className="flex flex-col items-start gap-1 p-4 rounded-xl border border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-variant)] text-left transition-shadow shadow-sm hover:shadow-md hover:border-[var(--primary)]/50"
            onClick={() => router.push(`/user_notes/${n.id}`)}
          >
            <span className="font-bold text-[var(--primary)] text-lg mb-1 line-clamp-1">
              {n.title}
            </span>
            <span className="text-xs text-[var(--foreground)]/70">
              {n.note_date ? format(new Date(n.note_date), "PPpp") : "Undated"}
            </span>
            {n.tags && n.tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {n.tags.map((tag) => (
                  <span
                    key={tag}
                    className="px-2 py-0.5 rounded-full text-xs bg-[var(--muted)] text-[var(--muted-foreground)]"
                  >
                    #{tag}
                  </span>
                ))}
              </div>
            )}
            {currentUserId && n.user_id !== currentUserId && owner && (
              <div className="mt-2 flex items-center text-xs text-[var(--foreground)]/70">
                {owner.image_url && (
                  <img
                    src={owner.image_url}
                    alt={owner.nickname || owner.email}
                    className="w-4 h-4 rounded-full mr-1"
                  />
                )}
                <span>Shared from {owner.nickname || owner.email}</span>
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
