"use client";
import { format } from "date-fns";
import { useState } from "react";

export interface ChangeFilter {
  type: string;
  author: string;
  from?: string;
  to?: string;
}

export default function ChangeFilterBar({
  availableTypes,
  filters,
  onChange,
}: {
  availableTypes: string[];
  filters: ChangeFilter;
  onChange: (f: ChangeFilter) => void;
}) {
  const [from, setFrom] = useState(filters.from || "");
  const [to, setTo] = useState(filters.to || "");

  return (
    <div className="flex flex-wrap items-end gap-2 text-sm">
      <div>
        <label className="block font-semibold text-xs">Type</label>
        <select
          className="border rounded-md px-2 py-1 bg-[var(--surface)]"
          value={filters.type}
          onChange={(e) => onChange({ ...filters, type: e.target.value })}
        >
          <option value="all">All</option>
          {availableTypes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="block font-semibold text-xs">Author</label>
        <select
          className="border rounded-md px-2 py-1 bg-[var(--surface)]"
          value={filters.author}
          onChange={(e) => onChange({ ...filters, author: e.target.value })}
        >
          <option value="all">All</option>
          <option value="user">User</option>
          <option value="agent">Agent</option>
        </select>
      </div>
      <div>
        <label className="block font-semibold text-xs">From</label>
        <input
          type="date"
          className="border rounded-md px-2 py-1 bg-[var(--surface)]"
          value={from}
          onChange={(e) => {
            setFrom(e.target.value);
            onChange({ ...filters, from: e.target.value });
          }}
        />
      </div>
      <div>
        <label className="block font-semibold text-xs">To</label>
        <input
          type="date"
          className="border rounded-md px-2 py-1 bg-[var(--surface)]"
          value={to}
          onChange={(e) => {
            setTo(e.target.value);
            onChange({ ...filters, to: e.target.value });
          }}
        />
      </div>
    </div>
  );
}
