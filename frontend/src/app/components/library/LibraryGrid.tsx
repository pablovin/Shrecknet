export default function LibraryGrid({ items, onItemClick }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-7">
      {items.map((it) => (
        <button
          key={it.id}
          className="group relative w-full bg-[var(--card-bg)]/90 rounded-2xl shadow-xl border border-[var(--primary)]/30 flex flex-col items-center px-6 py-7 cursor-pointer hover:shadow-2xl hover:border-[var(--accent)]/80 hover:scale-[1.025] active:scale-100 transition outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]"
          style={{ minHeight: 180 }}
          onClick={() => onItemClick(it)}
          tabIndex={0}
          aria-label={`Edit item ${it.name}`}
        >
          <div className="text-lg font-bold text-[var(--primary)] text-center truncate w-full mb-1">
            {it.name}
          </div>
          <div className="text-xs text-[var(--foreground)]/60 text-center truncate w-full mb-2">
            {it.system}
          </div>
          <div className="text-sm text-[var(--foreground)]/80 text-center line-clamp-3">
            {it.description}
          </div>
          <div className="absolute top-2 left-4 text-[10px] text-[var(--foreground)]/30 select-none font-mono">
            ID: {it.id}
          </div>
        </button>
      ))}
    </div>
  );
}
