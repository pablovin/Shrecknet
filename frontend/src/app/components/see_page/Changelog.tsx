"use client";
import { useMemo, useState } from "react";
import ChangelogEntry, { ChangeEntry } from "./ChangelogEntry";
import ChangeFilterBar, { ChangeFilter } from "./ChangeFilterBar";

export default function Changelog({ changes }: { changes: ChangeEntry[] }) {
  const [filters, setFilters] = useState<ChangeFilter>({
    type: "all",
    author: "all",
  });

  const availableTypes = useMemo(
    () => Array.from(new Set(changes.map((c) => c.change_type))),
    [changes]
  );

  const filtered = useMemo(() => {
    return changes
      .slice()
      .sort((a, b) => b.date.localeCompare(a.date))
      .filter((c) =>
        filters.type === "all" ? true : c.change_type === filters.type
      )
      .filter((c) =>
        filters.author === "all" ? true : c.author_type === filters.author
      )
      .filter((c) => {
        if (filters.from && new Date(c.date) < new Date(filters.from)) return false;
        if (filters.to && new Date(c.date) > new Date(filters.to)) return false;
        return true;
      });
  }, [changes, filters]);

  return (
    <div className="space-y-4">
      <ChangeFilterBar
        availableTypes={availableTypes}
        filters={filters}
        onChange={setFilters}
      />
      <div className="max-h-[60vh] overflow-y-auto space-y-4">
        {filtered.map((c) => (
          <ChangelogEntry key={c.id} change={c} />
        ))}
        {filtered.length === 0 && (
          <div className="text-sm text-center text-[var(--foreground)]/70">
            No changes found.
          </div>
        )}
      </div>
    </div>
  );
}
