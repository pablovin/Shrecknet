"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import AuthGuard from "../../../components/auth/AuthGuard";
import DashboardLayout from "../../../components/DashboardLayout";
import { API_URL } from "../../../lib/config";
import { useAuth } from "../../../components/auth/AuthProvider";
import { Loader2 } from "lucide-react";
import { useTranslation } from "../../../hooks/useTranslation";

export default function BookReaderPage() {
  const params = useParams();
  const id = params?.id as string;
  const { token } = useAuth();
  const { t } = useTranslation();

  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [item, setItem] = useState<any>(null);
  const [loading, setLoading] = useState(true);

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
      <DashboardLayout>
        <div className="w-full max-w-5xl mx-auto px-2 sm:px-6 py-8">
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
              <iframe src={fileUrl} className="w-full min-h-[80vh] border border-[var(--border)] rounded-xl" />
            </div>
          ) : (
            <div className="text-center text-[var(--foreground)]/70 py-20">{t("generic_error")}</div>
          )}
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
