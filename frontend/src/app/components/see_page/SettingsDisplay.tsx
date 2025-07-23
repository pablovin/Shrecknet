import { Info, Trash2 } from "lucide-react";

export default function SettingsDisplay({
  allowCrosslinks,
  ignoreCrosslink,
  allowCrossworld,
  onEdit,
  canEdit,
  onDelete,
  canDelete,
  deleteLabel,
}) {
  const settings = [
    {
      label: "Auto Links",
      enabled: allowCrosslinks,
      tooltip: "If enabled, we will automatically update the page content and add links to relevant pages.",
    },
    {
      label: "Hide This Page",
      enabled: ignoreCrosslink,
      tooltip: "If enabled, other pages will not add links to this page.",
    },
    {
      label: "Cross-World Links",
      enabled: allowCrossworld,
      tooltip: "If enabled, pages from other worlds can link this page.",
    }
  ];

  return (
    <div className="flex flex-wrap gap-2 items-center mt-2 mb-4">
      {settings.map((s) => (
        <span key={s.label} className={`flex items-center px-2 py-1 rounded-full border text-xs font-medium 
            ${s.enabled ? "bg-[var(--primary)]/20 border-[var(--primary)] text-[var(--primary)]" 
                        : "bg-[var(--surface)] border-[var(--border)] opacity-60"}
          `}
        >
          <span className="mr-1">{s.label}</span>
          <span className={`w-2 h-2 rounded-full ml-1 ${s.enabled ? "bg-green-500" : "bg-red-400"}`}></span>
          <span className="ml-2 group relative">
            <Info size={14} className="opacity-60 group-hover:opacity-100 cursor-pointer" />
            <span className="absolute z-50 left-1/2 group-hover:opacity-100 opacity-0 transition-all -translate-x-1/2 mt-1 min-w-max bg-[var(--surface-variant)] text-[var(--foreground)] text-xs p-2 rounded shadow-lg pointer-events-none opacity-0 group-hover:opacity-100 transition-all duration-200">
              {s.tooltip}
            </span>
          </span>
        </span>
      ))}
      {canEdit && (
        <button
          onClick={onEdit}
          className="ml-2 px-3 py-1 rounded-full bg-[var(--primary)] text-white text-xs font-semibold hover:bg-[var(--accent)] transition"
        >
          Edit Page
        </button>
      )}
      {canDelete && (
        <button
          onClick={onDelete}
          className="ml-2 flex items-center gap-1 px-3 py-1 rounded-full bg-red-600 text-white text-xs font-semibold shadow hover:bg-red-700 transition"
        >
          <Trash2 className="w-4 h-4" />
          {deleteLabel || 'Delete'}
        </button>
      )}
    </div>
  );
}
