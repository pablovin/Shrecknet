"use client";
import Image from "next/image";
import Link from "next/link";
import { GiCrystalBall } from "react-icons/gi";
import { Pencil, Trash2, Link as LinkIcon, Users } from "lucide-react";
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
  const formatDate = (d?: string) => (d ? new Date(d).toLocaleDateString() : "");
  const source = event.source_page_id ? pageMap[event.source_page_id] : undefined;

  return (
    <div className="relative pl-6 sm:pl-10">
      {/* Timeline orb */}
      <div className="absolute -left-3 sm:-left-5 top-5 z-10">
        <span className="w-5 h-5 rounded-full bg-[var(--background)] border-2 border-[var(--primary)] flex items-center justify-center shadow">
          <GiCrystalBall className="w-3 h-3 text-[var(--primary)]" />
        </span>
      </div>

      <div className="bg-[var(--surface)] border border-[var(--border)] rounded-xl shadow-sm p-4 sm:p-5 space-y-2 transition hover:shadow-md">
        {/* Top row: type + date + edit/delete */}
        <div className="flex items-center justify-between text-xs font-semibold">
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 rounded-full bg-[var(--primary)]/10 text-[var(--primary)] border border-[var(--primary)]/30">
              {event.event_type} • {formatDate(event.event_date)}
            </span>
            {authorName && (
              <span className="text-[var(--muted-foreground)] font-normal text-xs">Written by {authorName}</span>
            )}
          </div>
          {canEdit && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => onEdit(event)}
                className="text-[var(--primary)] hover:text-[var(--accent)]"
                title="Edit event"
              >
                <Pencil className="w-4 h-4" />
              </button>
              <button
                onClick={() => onDelete(event)}
                className="text-red-500 hover:text-red-700"
                title="Delete event"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>

        {/* Summary */}
        {event.summary && (
          <p className="text-sm text-[var(--foreground)] leading-relaxed">
            {event.summary}
          </p>
        )}

        {/* Related pages */}
        {event.related_page_ids && event.related_page_ids.length > 0 && (
          <div className="flex items-center gap-2 text-xs">
            <Users className="w-4 h-4 text-[var(--primary)]" />
            <span className="font-medium">Related Pages:</span>
            <div className="flex flex-wrap gap-2">
              {event.related_page_ids.map((id) => {
                const p = pageMap[id];
                if (!p) return null;
                return (
                  <WikiLinkHoverCard
                    key={id}
                    href={`/worlds/${p.gameworld_id}/concept/${p.concept_id}/page/${p.id}`}
                  >
                    <span className="flex items-center gap-1 px-2 py-0.5 rounded-full border border-[var(--primary)]/20 text-xs">
                      {p.logo && (
                        <Image
                          src={p.logo}
                          alt={p.name}
                          width={16}
                          height={16}
                          className="w-4 h-4 rounded-full object-cover"
                        />
                      )}
                      {p.name}
                    </span>
                  </WikiLinkHoverCard>
                );
              })}
            </div>
          </div>
        )}

        {/* Source page */}
        {source && (
          <div className="flex items-center gap-2 text-xs">
            <LinkIcon className="w-4 h-4 text-[var(--primary)]" />
            <span className="font-medium">Source:</span>
            <WikiLinkHoverCard
              href={`/worlds/${source.gameworld_id}/concept/${source.concept_id}/page/${source.id}`}
            >
              <span className="flex items-center gap-1 px-2 py-0.5 rounded-full border border-[var(--primary)]/20 text-xs">
                {source.logo && (
                  <Image
                    src={source.logo}
                    alt={source.name}
                    width={16}
                    height={16}
                    className="w-4 h-4 rounded-full object-cover"
                  />
                )}
                {source.name}
              </span>
            </WikiLinkHoverCard>
          </div>
        )}
      </div>
    </div>
  );
}
