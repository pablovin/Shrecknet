"use client";
import { useState, useEffect } from "react";
import ModalContainer from "../template/modalContainer";
import { useAuth } from "../auth/AuthProvider";
import { hasRole } from "@/app/lib/roles";
import { Combobox } from "@headlessui/react";
import Link from "next/link";
import { PlusCircle } from "lucide-react";
import { useUsers } from "@/app/lib/useUsers";
import { useAgents } from "@/app/lib/useAgents";
import EventCard, { Event as EventCardEvent } from "./EventCard";
import {
  createKeyEvent,
  updateKeyEvent,
  deleteKeyEvent,
  getPage,
} from "@/app/lib/pagesAPI";
import Image from "next/image";

// Re-exporting for consumers if needed
export type Event = EventCardEvent;

const emojiMap: Record<string, string> = {
  birth: "👶",
  death: "💀",
  battle: "⚔️",
  discovery: "🔍",
  founding: "🏰",
  disaster: "🔥",
  festival: "🎉",
};

const getEmoji = (type: string) => {
  const key = type.toLowerCase();
  return emojiMap[key] || "✨";
};

interface PageInfo {
  id: number;
  name: string;
  logo?: string;
  gameworld_id?: number;
  concept_id?: number;
}

function EventForm({
  initial,
  pages,
  eventTypes,
  onSubmit,
}: {
  initial?: Event;
  pages: PageInfo[];
  eventTypes: string[];
  onSubmit: (e: Event) => void;
}) {
  const [type, setType] = useState(initial?.event_type || eventTypes[0] || "");
  const [typeFilter, setTypeFilter] = useState("");
  const [date, setDate] = useState(
    initial?.event_date ? initial.event_date.substring(0, 10) : "",
  );
  const [summary, setSummary] = useState(initial?.summary || "");
  const [sourcePage, setSourcePage] = useState(
    initial?.source_page_id?.toString() || "",
  );
  const [related, setRelated] = useState<string[]>(
    (initial?.related_page_ids || []).map(String),
  );
  const [sourceFilter, setSourceFilter] = useState("");
  const [relatedFilter, setRelatedFilter] = useState("");

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit({
      ...initial,
      event_type: type,
      event_date: date ? new Date(date).toISOString() : undefined,
      summary,
      source_page_id: sourcePage ? Number(sourcePage) : undefined,
      related_page_ids: related.map((r) => Number(r)),
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex flex-col gap-1">
        <label className="font-semibold text-sm">Type</label>
        <Combobox value={type} onChange={(val) => setType(val)}>
          <div className="relative">
            <Combobox.Input
              className="border rounded-md px-3 py-1 bg-[var(--surface)] w-full"
              displayValue={(v: string) => v}
              onChange={(e) => {
                setTypeFilter(e.target.value);
                setType(e.target.value);
              }}
              placeholder="Select or type event type..."
            />
            <Combobox.Options className="absolute mt-1 max-h-60 w-full overflow-auto rounded bg-[var(--surface)] shadow-lg z-20 border">
              {eventTypes
                .filter((t) =>
                  t.toLowerCase().includes(typeFilter.toLowerCase()),
                )
                .map((t) => (
                  <Combobox.Option
                    key={t}
                    value={t}
                    className="px-2 py-1 cursor-pointer hover:bg-[var(--accent)]/20"
                  >
                    {t}
                  </Combobox.Option>
                ))}
            </Combobox.Options>
          </div>
        </Combobox>
        {sourcePage &&
          (() => {
            const p = pages.find((pg) => String(pg.id) === sourcePage);
            if (!p) return null;
            return (
              <Link
                href={`/worlds/${p.gameworld_id}/concept/${p.concept_id}/page/${p.id}`}
                target="_blank"
                className="text-xs underline text-[var(--primary)] mt-1"
              >
                View Selected Page
              </Link>
            );
          })()}
      </div>
      <div className="flex flex-col gap-1">
        <label className="font-semibold text-sm">Date</label>
        <input
          type="date"
          className="border rounded-md px-3 py-1 bg-[var(--surface)]"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="font-semibold text-sm">Summary</label>
        <textarea
          className="border rounded-md px-3 py-1 bg-[var(--surface)]"
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="font-semibold text-sm">Source Page</label>
        <Combobox value={sourcePage} onChange={(val) => setSourcePage(val)}>
          <div className="relative">
            <Combobox.Input
              className="border rounded-md px-3 py-1 bg-[var(--surface)] w-full"
              displayValue={(id: string) => {
                const p = pages.find((pg) => String(pg.id) === id);
                return p ? p.name : "";
              }}
              onChange={(e) => {
                setSourceFilter(e.target.value);
                setSourcePage(e.target.value);
              }}
              placeholder="Search page..."
            />
            <Combobox.Options className="absolute mt-1 max-h-60 w-full overflow-auto rounded bg-[var(--surface)] shadow-lg z-20 border">
              <Combobox.Option
                value=""
                className="px-2 py-1 cursor-pointer hover:bg-[var(--accent)]/20"
              >
                None
              </Combobox.Option>
              {pages
                .filter((p) =>
                  p.name.toLowerCase().includes(sourceFilter.toLowerCase()),
                )
                .map((p) => (
                  <Combobox.Option
                    key={p.id}
                    value={String(p.id)}
                    className="flex items-center gap-2 px-2 py-1 cursor-pointer hover:bg-[var(--accent)]/20"
                  >
                    {p.logo && (
                      <Image
                        src={p.logo}
                        alt={p.name}
                        width={16}
                        height={16}
                        className="w-4 h-4 rounded-full object-cover"
                      />
                    )}
                    <span className="flex-1">{p.name}</span>
                    <a
                      href={`/worlds/${p.gameworld_id}/concept/${p.concept_id}/page/${p.id}`}
                      target="_blank"
                      onClick={(e) => e.stopPropagation()}
                      className="text-xs underline text-[var(--primary)]"
                    >
                      Open
                    </a>
                  </Combobox.Option>
                ))}
            </Combobox.Options>
          </div>
        </Combobox>
      </div>
      <div className="flex flex-col gap-1">
        <label className="font-semibold text-sm">Related Pages</label>
        <Combobox
          value=""
          onChange={(val: number) => {
            if (!related.includes(String(val)))
              setRelated([...related, String(val)]);
          }}
        >
          <div className="relative">
            <Combobox.Input
              className="border rounded-md px-3 py-1 bg-[var(--surface)] w-full"
              onChange={(e) => setRelatedFilter(e.target.value)}
              displayValue={() => ""}
              placeholder="Search page..."
            />
            <Combobox.Options className="absolute mt-1 max-h-60 w-full overflow-auto rounded bg-[var(--surface)] shadow-lg z-20 border">
              {pages
                .filter(
                  (p) =>
                    p.name
                      .toLowerCase()
                      .includes(relatedFilter.toLowerCase()) &&
                    !related.includes(String(p.id)),
                )
                .map((p) => (
                  <Combobox.Option
                    key={p.id}
                    value={p.id}
                    className="flex items-center gap-2 px-2 py-1 cursor-pointer hover:bg-[var(--accent)]/20"
                  >
                    {p.logo && (
                      <Image
                        src={p.logo}
                        alt={p.name}
                        width={16}
                        height={16}
                        className="w-4 h-4 rounded-full object-cover"
                      />
                    )}
                    <span className="flex-1">{p.name}</span>
                    <a
                      href={`/worlds/${p.gameworld_id}/concept/${p.concept_id}/page/${p.id}`}
                      target="_blank"
                      onClick={(e) => e.stopPropagation()}
                      className="text-xs underline text-[var(--primary)]"
                    >
                      Open
                    </a>
                  </Combobox.Option>
                ))}
            </Combobox.Options>
          </div>
        </Combobox>
        <div className="flex flex-wrap gap-2 mt-2">
          {related.map((id) => {
            const p = pages.find((pg) => pg.id === Number(id));
            if (!p) return null;
            return (
              <span
                key={id}
                className="flex items-center gap-1 px-2 py-1 rounded-full bg-[var(--surface)] border text-xs"
              >
                <Link
                  href={`/worlds/${p.gameworld_id}/concept/${p.concept_id}/page/${p.id}`}
                  target="_blank"
                  className="flex items-center gap-1"
                >
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
                </Link>
                <button
                  type="button"
                  onClick={() => setRelated(related.filter((r) => r !== id))}
                  className="ml-1"
                >
                  ×
                </button>
              </span>
            );
          })}
        </div>
      </div>
      <button
        type="submit"
        className="px-4 py-2 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] hover:bg-[var(--accent)] transition"
      >
        Save
      </button>
    </form>
  );
}

export default function EventTimeline({
  pageId,
  events,
  pages = [],
  onUpdated,
}: {
  pageId: number;
  events: Event[];
  pages: PageInfo[];
  onUpdated: () => void;
}) {
  const { token, user } = useAuth();
  const canEdit = hasRole(user?.role, "writer");
  const [filter, setFilter] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [editEvent, setEditEvent] = useState<Event | null>(null);
  const [pageMap, setPageMap] = useState<Record<number, PageInfo>>({});

  const { users } = useUsers();
  const worldId =
    pages[0]?.gameworld_id || Object.values(pageMap)[0]?.gameworld_id;
  const { agents } = useAgents(worldId);

  const getAuthorName = (ev: Event) => {
    if (ev.author_type === "user") {
      return (
        users.find((u) => u.id === ev.author_id)?.nickname ||
        `User ${ev.author_id}`
      );
    }
    if (ev.author_type === "agent") {
      return (
        agents.find((a) => a.id === ev.author_id)?.name ||
        `Agent ${ev.author_id}`
      );
    }
    return ev.author_type || "";
  };

  // Fetch page details if not provided
  useEffect(() => {
    const ids = new Set<number>();
    events.forEach((e) => {
      e.related_page_ids?.forEach((id) => ids.add(id));
      if (e.source_page_id) ids.add(e.source_page_id);
    });
    ids.forEach((id) => {
      if (!pageMap[id] && token) {
        getPage(id, token)
          .then((p) =>
            setPageMap((m) => ({
              ...m,
              [id]: {
                id: p.id,
                name: p.name,
                logo: p.logo,
                gameworld_id: p.gameworld_id,
                concept_id: p.concept_id,
              },
            })),
          )
          .catch(() => {});
      }
    });
  }, [events, token]);

  const allPages = [...pages, ...Object.values(pageMap)];

  const filtered = events
    .filter((e) => (filter === "all" ? true : e.event_type === filter))
    .filter((e) => {
      if (!query) return true;
      const text = `${e.summary} ${e.event_date}`.toLowerCase();
      const relatedNames = e.related_page_ids
        ?.map((id) => pageMap[id]?.name || "")
        .join(" ")
        .toLowerCase();
      const sourceName = e.source_page_id
        ? pageMap[e.source_page_id]?.name || ""
        : "";
      return (
        text.includes(query.toLowerCase()) ||
        relatedNames?.includes(query.toLowerCase()) ||
        sourceName.toLowerCase().includes(query.toLowerCase())
      );
    })
    .sort((a, b) => (b.event_date || "").localeCompare(a.event_date || ""));
  const visible = showAll ? filtered : filtered.slice(0, 10);

  const handleSave = async (ev: Event) => {
    if (!token) return;
    try {
      if (ev.id) {
        await updateKeyEvent(ev.id, ev, token);
      } else {
        await createKeyEvent(
          pageId,
          { ...ev, page_id: pageId, author_type: "user", author_id: 0 },
          token,
        );
      }
      onUpdated();
      setShowForm(false);
      setEditEvent(null);
    } catch (err) {
      console.error("Failed to save", err);
    }
  };

  const handleDelete = async (ev: Event) => {
    if (!token || !ev.id) return;
    if (!confirm("Delete this event?")) return;
    try {
      await deleteKeyEvent(ev.id, token);
      onUpdated();
    } catch (err) {
      console.error("Failed", err);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-end flex-wrap gap-2 p-2 rounded-xl bg-[var(--surface-variant)]/60 border border-[var(--border)] shadow-inner font-serif">
        <select
          className="px-3 py-1 rounded-md bg-[var(--surface)] border border-[var(--border)]"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">🌐 All</option>
          {Array.from(new Set(events.map((e) => e.event_type))).map((t) => (
            <option key={t} value={t}>
              {getEmoji(t)} {t}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Search..."
          className="flex-1 px-3 py-1 rounded-md bg-[var(--surface)] border border-[var(--border)]"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {canEdit && (
          <button
            className="hidden sm:flex items-center gap-1 px-4 py-1 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] transition"
            onClick={() => setShowForm(true)}
          >
            <PlusCircle className="w-4 h-4" /> Add Event
          </button>
        )}
      </div>
      <div className="relative ml-6 sm:ml-8 max-h-[60vh] overflow-y-auto">
        <div className="absolute left-0 top-0 bottom-0 w-1 bg-[var(--primary)] rounded-full"></div>
        <div className="space-y-8">
          {visible.map((e) => (
            <EventCard
              key={e.id}
              event={e}
              pageMap={pageMap}
              canEdit={canEdit}
              onEdit={(ev) => {
                setEditEvent(ev);
                setShowForm(true);
              }}
              onDelete={handleDelete}
              authorName={getAuthorName(e)}
            />
          ))}
        </div>
        {!showAll && filtered.length > 10 && (
          <button
            className="text-sm text-[var(--primary)] underline mt-2"
            onClick={() => setShowAll(true)}
          >
            Load more ({filtered.length - 10} more)
          </button>
        )}
        {filtered.length === 0 && (
          <div className="text-sm text-center text-[var(--foreground)]/70">
            No events found.
          </div>
        )}
      </div>
      {canEdit && (
        <button
          className="sm:hidden fixed bottom-6 right-6 z-20 flex items-center justify-center w-12 h-12 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] shadow-lg"
          onClick={() => setShowForm(true)}
        >
          <PlusCircle className="w-6 h-6" />
        </button>
      )}
      {showForm && (
        <ModalContainer
          title={editEvent ? "Edit Event" : "Add Event"}
          onClose={() => {
            setShowForm(false);
            setEditEvent(null);
          }}
        >
          <EventForm
            initial={editEvent || { event_type: "" }}
            pages={allPages}
            eventTypes={Array.from(new Set(events.map((e) => e.event_type)))}
            onSubmit={handleSave}
          />
        </ModalContainer>
      )}
    </div>
  );
}
