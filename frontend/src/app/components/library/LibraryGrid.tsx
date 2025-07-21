import { Info } from "lucide-react";
import { useState } from "react";

export default function LibraryGrid({ items, onItemClick, onEmbed, jobsByItem, embeddingId, readOnly = false }) {
  const [hoveredId, setHoveredId] = useState<number | null>(null);

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-7">
      {items.map((it) => (
        <div
          key={it.id}
          onMouseEnter={() => setHoveredId(it.id)}
          onMouseLeave={() => setHoveredId(null)}
          className="group relative w-full rounded-2xl shadow-lg border border-[var(--primary)]/30 bg-gradient-to-br from-[var(--surface)]/90 to-[var(--surface-variant)]/80 backdrop-blur-md
            flex flex-col items-center px-6 py-6 cursor-pointer transition-transform hover:scale-[1.03] hover:shadow-xl hover:border-[var(--accent)]/80"
          style={{ minHeight: 260 }}
        >
          {/* Clickable Area */}
          <button
            onClick={() => onItemClick(it)}
            className="absolute inset-0 z-10"
            aria-label={readOnly ? `Open item ${it.name}` : `Edit item ${it.name}`}
          />

          {/* Floating Info Icon */}
          <div className="absolute top-2 right-3 z-20 text-[var(--primary)]">
            <Info className="w-4 h-4 opacity-70 group-hover:opacity-100 transition" />
          </div>

          {/* Tooltip Card */}
          {hoveredId === it.id && (
            <div className="absolute right-3 top-6 z-30 bg-[var(--card-bg)] border border-[var(--border)] text-sm p-3 rounded-lg shadow-lg w-64 text-left animate-fade-in">
              <div className="font-bold text-[var(--primary)] mb-1">{it.name}</div>
              <div className="text-xs text-[var(--foreground)]/80 mb-1">{it.system}</div>
              <div className="text-xs text-[var(--foreground)]/70 line-clamp-4">{it.description}</div>
            </div>
          )}

          {/* Title - FULL, WRAPPED */}
          <div className="z-20 text-center font-serif font-semibold text-[var(--primary)] text-base mb-2 pointer-events-none break-words leading-tight">
            {it.name}
          </div>

          {/* Cover */}
          {it.cover_url && (
            <img
              src={it.cover_url.startsWith("/") ? it.cover_url : `/${it.cover_url}`}
              alt="cover"
              className="z-10 w-full h-40 object-cover rounded-xl mb-3 border border-[var(--border)] shadow-sm pointer-events-none"
            />
          )}

          {/* System Chip */}
          <div className="z-10 text-xs text-[var(--primary)]/80 bg-[var(--surface-variant)] px-2 py-1 rounded-full font-medium mb-2 pointer-events-none">
            {it.system}
          </div>

          {/* Description (optional fallback) */}
          <div className="z-10 text-sm text-[var(--foreground)]/80 text-center line-clamp-2 pointer-events-none">
            {it.description}
          </div>

          {/* Vector Info */}
          {!readOnly && it.vector_db_update_date && (
            <div className="z-10 text-[10px] text-[var(--foreground)]/60 mt-1 font-mono pointer-events-none">
              Vector DB: {new Date(it.vector_db_update_date).toLocaleString()}
            </div>
          )}

          {/* Embed Button */}
          {!readOnly && (
            <div className="z-20 mt-3 flex flex-col items-center gap-1 w-full">
              <button
                className="px-4 py-1.5 rounded-lg font-medium text-sm bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md transition
                  hover:bg-[var(--accent)] hover:text-[var(--background)] border border-[var(--primary)]"
                onClick={(e) => {
                  e.stopPropagation();
                  onEmbed?.(it);
                }}
                disabled={
                  embeddingId === it.id ||
                  (jobsByItem && jobsByItem[it.id]?.some((j) => j.status !== "done" && j.status !== "queued"))
                }
              >
                {embeddingId === it.id
                  ? "Embedding..."
                  : jobsByItem && jobsByItem[it.id]?.some((j) => j.status !== "done" && j.status !== "queued")
                  ? "Running..."
                  : "Embed"}
              </button>
              {jobsByItem && jobsByItem[it.id] && (
                <div className="text-[10px] text-[var(--foreground)]/80 font-mono">
                  {jobsByItem[it.id][0].progress ?? 0}%
                </div>
              )}
            </div>
          )}

          {/* ID Tag */}
          {!readOnly && (
            <div className="absolute top-2 left-4 text-[10px] text-[var(--foreground)]/40 select-none font-mono z-0 pointer-events-none">
              ID: {it.id}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
