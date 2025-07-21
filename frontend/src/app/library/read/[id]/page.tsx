"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import AuthGuard from "../../../components/auth/AuthGuard";
import Link from "next/link";
import { useLibraryItems } from "../../../lib/useLibraryItems";
import { API_URL } from "../../../lib/config";
import { useAuth } from "../../../components/auth/AuthProvider";
import { Loader2 } from "lucide-react";
import { useTranslation } from "../../../hooks/useTranslation";

export default function BookReaderPage() {
  const params = useParams();
  const id = params?.id as string;
  const { token } = useAuth();
  const { t } = useTranslation();
  const { items } = useLibraryItems();

  const [search, setSearch] = useState("");
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [item, setItem] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const filtered = items
    .filter(
      (it) =>
        it.name.toLowerCase().includes(search.toLowerCase()) ||
        it.description?.toLowerCase().includes(search.toLowerCase())
    )
    .slice(0, 5);

  useEffect(() => {
    if (!id || !token) return;
    async function load() {
      try {
        const itemRes = await fetch(`${API_URL}/library/${id}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const itemData = await itemRes.json();
        setItem(itemData);
        const fileRes = await fetch(`${API_URL}/library/${id}/download`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const blob = await fileRes.blob();
        const url = URL.createObjectURL(blob);
        setFileUrl(url);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    load();
    return () => {
      if (fileUrl) URL.revokeObjectURL(fileUrl);
    };
  }, [id, token]);

  return (
    <AuthGuard>
      <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-2 sm:px-6 py-4">
        <div className="max-w-full mx-auto flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <Link
              href="/library"
              className="px-4 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition"
            >
              {t("back_to_library")}
            </Link>
            <div className="relative">
              <input
                className="px-4 py-2 w-64 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] placeholder-[var(--primary)]/60 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] text-base shadow transition"
                placeholder={t("search_library_placeholder")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {search && filtered.length > 0 && (
                <div className="absolute right-0 z-10 mt-1 w-64 max-h-60 overflow-y-auto bg-[var(--background)] border border-[var(--primary)] rounded-xl shadow">
                  {filtered.map((it) => (
                    <Link
                      key={it.id}
                      href={`/library/read/${it.id}`}
                      className="block px-3 py-2 hover:bg-[var(--primary)]/10"
                      onClick={() => setSearch("")}
                    >
                      {it.name}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </div>
          {loading ? (
            <div className="flex flex-col items-center gap-4 py-20">
              <Loader2 className="w-8 h-8 animate-spin text-[var(--primary)]" />
              <div className="w-full h-2 bg-[var(--border)] rounded-full overflow-hidden">
                <div className="h-full bg-[var(--primary)] animate-pulse" style={{ width: "100%" }} />
              </div>
              <div className="text-sm text-[var(--foreground)]/70">{t("loading_book")}</div>
            </div>
          ) : fileUrl ? (
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between gap-4">
                <h1 className="text-2xl font-bold text-[var(--primary)]">{item?.name}</h1>
                <a
                  href={fileUrl}
                  download={item?.name}
                  className="px-4 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition"
                >
                  Download
                </a>
              </div>
              <iframe src={fileUrl} className="w-full min-h-screen border border-[var(--border)] rounded-xl" />
            </div>
          ) : (
            <div className="text-center text-[var(--foreground)]/70 py-20">{t("generic_error")}</div>
          )}
        </div>
      </div>
    </AuthGuard>
  );
}
