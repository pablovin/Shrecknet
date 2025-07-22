"use client";
import { format } from "date-fns";
import { useState } from "react";
import MarkdownIt from "markdown-it";

const md = new MarkdownIt();

export interface ChangeEntry {
  id: number;
  date: string;
  change_type: string;
  author_type: string;
  author_id: number;
  values?: any;
}

export default function ChangelogEntry({ change }: { change: ChangeEntry }) {
  const [open, setOpen] = useState(false);
  const content = change.values?.content || change.values?.summary || "";

  return (
    <div className="bg-[var(--surface)] border border-[var(--border)] rounded-xl shadow p-4 space-y-2">
      <div className="flex justify-between items-center text-sm">
        <div className="font-semibold flex items-center gap-2">
          <span>{format(new Date(change.date), "PPpp")}</span>
          <span className="px-2 py-0.5 rounded-full text-xs bg-[var(--primary)]/20 border border-[var(--primary)]/40">
            {change.author_type}
          </span>
        </div>
        <div className="text-xs text-[var(--foreground)]/70">{change.change_type}</div>
      </div>
      {content && (
        <div>
          <button
            className="text-[var(--primary)] text-xs underline"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "Hide" : "Show"} Details
          </button>
          {open && (
            <div
              className="mt-2 prose prose-sm max-w-none text-[var(--foreground)]"
              dangerouslySetInnerHTML={{ __html: md.render(content) }}
            />
          )}
        </div>
      )}
    </div>
  );
}
