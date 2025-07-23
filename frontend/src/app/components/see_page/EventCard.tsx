import Image from "next/image";
import Link from "next/link";
import { GiCrystalBall } from "react-icons/gi";
import WikiLinkHoverCard from "../editor/WikiLinkHoverCard";

export interface Event {
  id?: number;
  page_id?: number;
  event_type: string;
  event_date?: string;
  summary?: string;
  source_page_id?: number | null;
  related_page_ids?: number[];
  author_type?: string;
  author_id?: number;
  added_at?: string;
}

interface PageInfo {
  id: number;
  name: string;
  logo?: string;
  gameworld_id?: number;
  concept_id?: number;
}

export default function EventCard({
  event,
  pageMap,
  canEdit,
  onEdit,
  onDelete,
  authorName,
}: {
  event: Event;
  pageMap: Record<number, PageInfo>;
  canEdit: boolean;
  onEdit: (e: Event) => void;
  onDelete: (e: Event) => void;
  authorName?: string;
}) {
  const formatDate = (d?: string) =>
    d ? new Date(d).toLocaleDateString() : "";
  const source = event.source_page_id
    ? pageMap[event.source_page_id]
    : undefined;

  return (
    <div className="relative pl-8 sm:pl-12">
      <div className="absolute -left-4 sm:-left-6 top-4 z-10">
        <span className="w-6 h-6 rounded-full bg-[var(--background)] border-2 border-[var(--primary)] flex items-center justify-center shadow">
          <GiCrystalBall className="w-4 h-4 text-[var(--primary)]" />
        </span>
      </div>
      <div className="bg-[var(--surface-variant)]/80 border border-[var(--border)] rounded-2xl shadow-md p-4 backdrop-blur-sm">
        <div className="flex justify-between items-center mb-2">
          <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-[var(--primary)]/20 text-[var(--primary)] border border-[var(--primary)]/30">
            {event.event_type}
          </span>
          <span className="text-xs opacity-80">{formatDate(event.event_date)}</span>
        </div>
        {event.summary && (
          <p className="prose-sm mb-2 text-[var(--foreground)]">{event.summary}</p>
        )}
        {event.related_page_ids && event.related_page_ids.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {event.related_page_ids.map((id) => {
              const p = pageMap[id];
              if (!p) return null;
              const href = `/worlds/${p.gameworld_id}/concept/${p.concept_id}/page/${p.id}`;
              return (
                <WikiLinkHoverCard href={href} key={id}>
                  <span className="flex items-center gap-1 px-2 py-1 rounded-full bg-[var(--surface)] border text-xs">
                    {p.logo && (
                      <Image src={p.logo} alt={p.name} width={16} height={16} className="w-4 h-4 rounded-full object-cover" />
                    )}
                    {p.name}
                  </span>
                </WikiLinkHoverCard>
              );
            })}
          </div>
        )}
        {source && (
          <div className="flex items-center gap-2 text-xs mb-2">
            <span className="font-semibold">Source:</span>
            <WikiLinkHoverCard
              href={`/worlds/${source.gameworld_id}/concept/${source.concept_id}/page/${source.id}`}
            >
              <span className="flex items-center gap-1 px-2 py-1 rounded-full bg-[var(--surface)] border text-xs">
                {source.logo && (
                  <Image src={source.logo} alt="src" width={16} height={16} className="w-4 h-4 rounded-full object-cover" />
                )}
                {source.name}
              </span>
            </WikiLinkHoverCard>
          </div>
        )}
        {authorName && (
          <div className="text-xs text-right text-[var(--foreground)]/70 mt-1">
            Written by {authorName}
          </div>
        )}
        {canEdit && (
          <div className="flex justify-end gap-2 text-xs mt-2">
            <button
              onClick={() => onEdit(event)}
              className="text-[var(--primary)] hover:underline"
            >
              Edit
            </button>
            <button
              onClick={() => onDelete(event)}
              className="text-red-600 hover:underline"
            >
              Delete
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
