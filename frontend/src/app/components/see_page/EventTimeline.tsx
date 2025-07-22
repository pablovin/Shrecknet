"use client";
import { useState, useEffect } from "react";
import ModalContainer from "../template/modalContainer";
import { useAuth } from "../auth/AuthProvider";
import {
  createKeyEvent,
  updateKeyEvent,
  deleteKeyEvent,
  getPage,
} from "@/app/lib/pagesAPI";
import Image from "next/image";

interface Event {
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
}

const EVENT_ICONS: Record<string, string> = {
  battle: "🗡️",
  war: "⚔️",
  lore: "📜",
  magic: "✨",
  item: "📦",
};

function EventForm({
  initial,
  pages,
  onSubmit,
}: {
  initial?: Event;
  pages: PageInfo[];
  onSubmit: (e: Event) => void;
}) {
  const [type, setType] = useState(initial?.event_type || "battle");
  const [date, setDate] = useState(
    initial?.event_date ? initial.event_date.substring(0, 10) : ""
  );
  const [summary, setSummary] = useState(initial?.summary || "");
  const [sourcePage, setSourcePage] = useState(
    initial?.source_page_id?.toString() || ""
  );
  const [related, setRelated] = useState<string[]>(
    (initial?.related_page_ids || []).map(String)
  );

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
        <select
          className="border rounded-md px-3 py-1 bg-[var(--surface)]"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          {Object.keys(EVENT_ICONS).map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
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
        <select
          className="border rounded-md px-3 py-1 bg-[var(--surface)]"
          value={sourcePage}
          onChange={(e) => setSourcePage(e.target.value)}
        >
          <option value="">None</option>
          {pages.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="font-semibold text-sm">Related Pages</label>
        <select
          multiple
          className="border rounded-md px-3 py-1 bg-[var(--surface)] h-32"
          value={related}
          onChange={(e) =>
            setRelated(
              Array.from(e.target.selectedOptions).map((o) => o.value)
            )
          }
        >
          {pages.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
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
  const { token } = useAuth();
  const [filter, setFilter] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editEvent, setEditEvent] = useState<Event | null>(null);
  const [pageMap, setPageMap] = useState<Record<number, PageInfo>>({});

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
            setPageMap((m) => ({ ...m, [id]: { id: p.id, name: p.name, logo: p.logo } }))
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
      const sourceName = e.source_page_id ? pageMap[e.source_page_id]?.name || "" : "";
      return (
        text.includes(query.toLowerCase()) ||
        relatedNames?.includes(query.toLowerCase()) ||
        sourceName.toLowerCase().includes(query.toLowerCase())
      );
    })
    .sort((a, b) => (a.event_date || "").localeCompare(b.event_date || ""));

  const handleSave = async (ev: Event) => {
    if (!token) return;
    try {
      if (ev.id) {
        await updateKeyEvent(ev.id, ev, token);
      } else {
        await createKeyEvent(pageId, { ...ev, page_id: pageId, author_type: "user", author_id: 0 }, token);
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
      <div className="flex items-center gap-2 flex-wrap">
        <select
          className="border rounded-md px-3 py-1 bg-[var(--surface)]"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">All</option>
          {Array.from(new Set(events.map((e) => e.event_type))).map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Search..."
          className="border rounded-md px-3 py-1 bg-[var(--surface)] flex-1"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          className="px-3 py-1 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] hover:bg-[var(--accent)] transition"
          onClick={() => setShowForm(true)}
        >
          Add Event
        </button>
      </div>
      <div className="relative border-l-2 border-[var(--primary)] ml-4 pl-6 max-h-[60vh] overflow-y-auto">
        {filtered.map((e) => (
          <div key={e.id} className="mb-8 relative group">
            <span className="absolute -left-3 top-2 text-xl">
              {EVENT_ICONS[e.event_type] || "📜"}
            </span>
            <div className="bg-[var(--surface-variant)] border border-[var(--border)] rounded-xl p-4 shadow-md">
              <div className="flex justify-between items-center mb-2">
                <div className="font-semibold text-sm flex items-center gap-1">
                  {EVENT_ICONS[e.event_type] || "📜"} {e.event_type}
                </div>
                <div className="text-xs text-[var(--foreground)]/70">
                  {e.event_date ? new Date(e.event_date).toLocaleDateString() : ""}
                </div>
              </div>
              {e.summary && <p className="mb-2 text-sm">{e.summary}</p>}
              {e.related_page_ids && e.related_page_ids.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-2">
                  {e.related_page_ids.map((id) => {
                    const p = pageMap[id];
                    if (!p) return null;
                    return (
                      <span key={id} className="flex items-center gap-1 px-2 py-1 rounded-full bg-[var(--surface)] border text-xs">
                        {p.logo && (
                          <Image src={p.logo} alt={p.name} width={16} height={16} className="w-4 h-4 rounded-full object-cover" />
                        )}
                        {p.name}
                      </span>
                    );
                  })}
                </div>
              )}
              {e.source_page_id && pageMap[e.source_page_id] && (
                <div className="flex items-center gap-2 text-xs mb-2">
                  <span className="font-semibold">Source:</span>
                  {pageMap[e.source_page_id].logo && (
                    <Image src={pageMap[e.source_page_id].logo!} alt="src" width={16} height={16} className="w-4 h-4 rounded-full object-cover" />
                  )}
                  {pageMap[e.source_page_id].name}
                </div>
              )}
              <div className="flex justify-end gap-2 text-xs">
                <button onClick={() => { setEditEvent(e); setShowForm(true); }} className="text-[var(--primary)] hover:underline">Edit</button>
                <button onClick={() => handleDelete(e)} className="text-red-600 hover:underline">Delete</button>
              </div>
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="text-sm text-center text-[var(--foreground)]/70">No events found.</div>
        )}
      </div>
      {showForm && (
        <ModalContainer title={editEvent ? "Edit Event" : "Add Event"} onClose={() => { setShowForm(false); setEditEvent(null); }}>
          <EventForm initial={editEvent || { event_type: "battle" }} pages={allPages} onSubmit={handleSave} />
        </ModalContainer>
      )}
    </div>
  );
}
