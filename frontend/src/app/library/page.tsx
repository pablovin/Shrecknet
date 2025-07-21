"use client";
import { useState } from "react";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useLibraryItems } from "../lib/useLibraryItems";
import LibraryGrid from "../components/library/LibraryGrid";
import { useTranslation } from "../hooks/useTranslation";
import { BookOpen } from "lucide-react";

export default function LibraryPage() {
  const { items, isLoading } = useLibraryItems();
  const [search, setSearch] = useState("");
  const [systemFilter, setSystemFilter] = useState("");
  const { t } = useTranslation();

  const filtered = (items || []).filter(
    (it) =>
      (!systemFilter || it.system === systemFilter) &&
      (it.name.toLowerCase().includes(search.toLowerCase()) ||
        it.description?.toLowerCase().includes(search.toLowerCase()))
  );

  const systems = Array.from(new Set(filtered.map((i) => i.system)));

  function handleOpen(item: any) {
    window.open(`/library/read/${item.id}`, "_blank");
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-4 sm:px-6 py-10 transition-colors duration-300">
          <div className="max-w-6xl mx-auto flex flex-col gap-10">
            {/* Title Section */}
            <div className="text-center flex flex-col items-center gap-2 relative">
              <div className="bg-gradient-to-br from-purple-400 to-fuchsia-500 text-white p-3 rounded-full shadow-lg animate-fade-in mb-2">
                <BookOpen className="w-8 h-8" />
              </div>
              <h1 className="font-serif text-4xl md:text-5xl font-bold text-[var(--primary)] tracking-tight">
                {t("library_welcome")}
              </h1>
              <p className="text-base md:text-lg text-[var(--foreground)]/70">
                {t("library_intro")}
              </p>
            </div>

           {/* Filters */}
<div className="flex flex-wrap gap-4 items-center justify-center bg-[var(--surface-variant)]/70 rounded-2xl px-6 py-5 shadow-inner border border-[var(--border)]">
  {/* Search */}
  <div className="relative">
    <input
      className="pl-10 pr-4 py-2 rounded-full border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] placeholder-[var(--primary)]/60
        focus:outline-none focus:ring-2 focus:ring-[var(--primary)] text-base shadow-md transition w-64"
      placeholder={t("search_library_placeholder")}
      value={search}
      onChange={(e) => setSearch(e.target.value)}
    />
    <svg
      className="w-5 h-5 absolute left-3 top-2.5 text-[var(--primary)]/60 pointer-events-none"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M15 11a4 4 0 10-8 0 4 4 0 008 0z" />
    </svg>
  </div>

      {/* System Filter */}
      <div className="relative">
        <select
          className="appearance-none pr-10 pl-4 py-2 rounded-full border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] text-base shadow-md transition w-48 cursor-pointer"
          value={systemFilter}
          onChange={(e) => setSystemFilter(e.target.value)}
        >
          <option value="">{t("all_systems") + " ✨"}</option>
          {Array.from(new Set(items.map((i) => i.system))).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <svg
          className="w-4 h-4 absolute right-3 top-3 text-[var(--primary)]/60 pointer-events-none"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </div>
    </div>


            {/* Content */}
            {isLoading ? (
              <div className="text-center py-16 text-lg text-[var(--primary)] animate-pulse">
                {t("loading_library")}
              </div>
            ) : systems.length === 0 ? (
              <div className="text-center py-16 text-[var(--primary)] text-xl font-semibold italic opacity-70">
                📚 {t("no_results_found")} — try a different realm.
              </div>
            ) : (
              systems.map((sys) => (
                <div key={sys} className="flex flex-col gap-4 mb-10">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 bg-[var(--primary)] rounded-full" />
                    <h2 className="text-xl md:text-2xl font-bold text-[var(--primary)] font-serif tracking-tight">
                      {sys}
                    </h2>
                    <div className="flex-grow h-px bg-[var(--border)]" />
                  </div>
                  <LibraryGrid
                    items={filtered.filter((it) => it.system === sys)}
                    onItemClick={handleOpen}
                    readOnly
                  />
                </div>
              ))
            )}
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
