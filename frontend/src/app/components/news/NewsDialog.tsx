"use client";
import { useState } from "react";
import ModalContainer from "../template/modalContainer";
import { useTranslation } from "../../hooks/useTranslation";
import { Megaphone, Sparkles, Info } from "lucide-react";

// Helper: get unique types, default to All
function getNewsTypes(news) {
  const types = Array.from(new Set(news.map((n) => n.type))).filter(Boolean);
  return ["All", ...types];
}

const TYPE_ICONS = {
  Announcement: <Megaphone className="w-5 h-5" />,
  Event: <Sparkles className="w-5 h-5" />,
  Update: <Info className="w-5 h-5" />,
};

export default function NewsDialog({ open, onClose, news }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState("All");

  if (!open) return null;

  const types = news ? getNewsTypes(news) : ["All"];
  const filteredNews = tab === "All"
    ? news
    : news.filter((n) => n.type === tab);

  return (
    <ModalContainer
      title={t("newsboard")}
      onClose={onClose}
      className={`
        w-full max-w-5xl
        md:w-[80vw] md:max-w-[80vw] 
        rounded-3xl p-0 overflow-hidden
        !min-h-[60vh]
      `}
      style={{ maxHeight: "90vh" }}
      backdropClass="bg-black/40"
      contentClass="p-0"
    >
      <div className="w-full h-full flex flex-col bg-[#faf7ff]">
        {/* Tabs */}
        <div className="w-full flex gap-1 sm:gap-3 px-6 pt-6 border-b border-[var(--primary)]/20 bg-white/80 sticky top-0 z-10">
          {types.map((type) => (
            <button
              key={type}
              onClick={() => setTab(type)}
              className={`
                px-4 py-2 rounded-t-xl text-base font-bold
                flex items-center gap-2
                ${
                  tab === type
                    ? "bg-[var(--primary)] text-white shadow"
                    : "text-[var(--primary)] bg-[var(--primary)]/10 hover:bg-[var(--primary)]/20"
                }
                transition-all
              `}
            >
              {/* Optional icon by type */}
              {TYPE_ICONS[type] || null}
              {type === "All" ? t("all") : type}
            </button>
          ))}
        </div>
        {/* News List */}
        <div className="flex-1 overflow-y-auto px-6 py-6">
          {(!filteredNews || filteredNews.length === 0) ? (
            <div className="text-center text-lg text-[var(--muted-foreground)] opacity-80 py-12">
              {t("no_news")}
            </div>
          ) : (
            <ul className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-7">
              {filteredNews.map((n) => (
                <li
                  key={n.id}
                  className={`
                    rounded-2xl border-2 border-[var(--primary)] bg-white/80 p-5 shadow-md
                    flex flex-col gap-2 hover:shadow-xl hover:border-[var(--primary-dark)]/90 transition
                  `}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-serif font-bold text-[var(--primary)] text-lg truncate">
                      {n.title}
                    </span>
                    <span className="text-xs text-[var(--muted-foreground)]">
                      {new Date(n.created_at).toLocaleDateString()}
                    </span>
                  </div>
                  <span className="inline-flex items-center gap-1 text-xs text-[var(--primary)] font-semibold bg-[var(--primary)]/10 rounded-full px-2 py-1 w-fit mb-1">
                    {TYPE_ICONS[n.type] || null} {n.type}
                  </span>
                  <p className="text-sm text-[var(--foreground)]/90 whitespace-pre-line">
                    {n.description}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </ModalContainer>
  );
}
