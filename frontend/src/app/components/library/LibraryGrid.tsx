export default function LibraryGrid({ items, onItemClick, onEmbed, jobsByItem, embeddingId, readOnly = false }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-7">
      {items.map((it) => (
        <div
          key={it.id}
          className="group relative w-full bg-[var(--card-bg)]/90 rounded-2xl shadow-xl border border-[var(--primary)]/30 flex flex-col items-center px-6 py-7 cursor-pointer hover:shadow-2xl hover:border-[var(--accent)]/80 hover:scale-[1.025] active:scale-100 transition"
          style={{ minHeight: 200 }}
        >
          <button
            onClick={() => onItemClick(it)}
            className="absolute inset-0"
            aria-label={readOnly ? `Open item ${it.name}` : `Edit item ${it.name}`}
          />
          <div className="text-lg font-bold text-[var(--primary)] text-center truncate w-full mb-1 pointer-events-none">
            {it.name}
          </div>
          {it.cover_url && (
            <img
              src={it.cover_url.startsWith("/") ? it.cover_url : `/${it.cover_url}`}
              alt="cover"
              className="w-full h-40 object-cover rounded-lg mb-2 pointer-events-none"
            />
          )}
          <div className="text-xs text-[var(--foreground)]/60 text-center truncate w-full mb-2 pointer-events-none">
            {it.system}
          </div>
          <div className="text-sm text-[var(--foreground)]/80 text-center line-clamp-3 pointer-events-none">
            {it.description}
          </div>
          {!readOnly && it.vector_db_update_date && (
            <div className="text-[10px] text-[var(--foreground)]/70 mt-1 pointer-events-none">
              Vector DB: {new Date(it.vector_db_update_date).toLocaleString()}
            </div>
          )}
          {!readOnly && (
            <div className="mt-2 flex flex-col gap-1 w-full items-center z-10">
              <button
                className="px-3 py-1 rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)] text-sm shadow disabled:opacity-50"
                onClick={(e) => { e.stopPropagation(); onEmbed?.(it); }}
                disabled={embeddingId === it.id || (jobsByItem && jobsByItem[it.id]?.some(j => j.status !== "done" && j.status !== "queued"))}
              >
                {embeddingId === it.id
                  ? "Embedding..."
                  : jobsByItem && jobsByItem[it.id]?.some(j => j.status !== "done" && j.status !== "queued")
                  ? "Running..."
                  : "Embed"}
              </button>
              {jobsByItem && jobsByItem[it.id] && (
                <div className="text-[10px] text-[var(--foreground)]/80">
                  {jobsByItem[it.id][0].progress ?? 0}%
                </div>
              )}
            </div>
          )}
          {!readOnly && (
            <div className="absolute top-2 left-4 text-[10px] text-[var(--foreground)]/30 select-none font-mono pointer-events-none">
              ID: {it.id}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
