"use client";
import { useState } from "react";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useLibraryItems } from "../lib/useLibraryItems";
import LibraryGrid from "../components/library/LibraryGrid";
import { useTranslation } from "../hooks/useTranslation";

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
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-2 sm:px-6 py-8">
          <div className="max-w-6xl mx-auto flex flex-col gap-6">
            <div className="text-center flex flex-col gap-2">
              <h1 className="font-serif text-3xl md:text-5xl font-bold text-[var(--primary)] tracking-tight">
                {t("library_welcome")}
              </h1>
              <p className="text-base md:text-lg text-[var(--foreground)]/70">
                {t("library_intro")}
              </p>
            </div>
            <div className="flex flex-wrap gap-4 items-center justify-center bg-[var(--surface-variant)]/60 rounded-xl p-3">
              <input
                className="px-4 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] placeholder-[var(--primary)]/60 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] text-base shadow transition"
                placeholder={t("search_library_placeholder")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <select
                className="px-3 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] text-base shadow transition"
                value={systemFilter}
                onChange={(e) => setSystemFilter(e.target.value)}
              >
                <option value="">All systems</option>
                {Array.from(new Set(items.map((i) => i.system))).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            {isLoading ? (
              <div className="text-center py-10">{t("loading_library")}</div>
            ) : systems.length === 0 ? (
              <div className="text-center py-10">{t("no_results_found")}</div>
            ) : (
              systems.map((sys) => (
                <div key={sys} className="flex flex-col gap-4 mb-6">
                  <h2 className="text-xl font-bold text-[var(--primary)]">{sys}</h2>
                  <LibraryGrid items={filtered.filter((it) => it.system === sys)} onItemClick={handleOpen} readOnly />
                </div>
              ))
            )}
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
