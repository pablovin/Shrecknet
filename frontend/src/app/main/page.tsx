"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import { useTranslation } from "../hooks/useTranslation";
import Link from "next/link";
import {
  Globe,
  BookOpenText,
  Users,
  Sparkles,
  Bot,
  PenLine,
  Hammer,
  Settings,
  StickyNote,
  ScrollText,
  Library,
} from "lucide-react";
import { useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";

// --- Error Banner ---
function ErrorBannerClient() {
  const params = useSearchParams();
  const error = params.get("error");
  if (error === "unauthorized") {
    return (
      <div className="text-center bg-red-100 text-red-700 border border-red-300 px-4 py-2 rounded">
        You are not authorized to view this page.
      </div>
    );
  }
  return null;
}

// --- Glyph button ---
function DashGlyph({ href, icon, title, isAI, iconColor, description }) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      className="relative flex flex-col items-center"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      tabIndex={0}
      aria-label={title}
    >
      <Link
        href={href}
        className={`
          group relative flex flex-col items-center justify-center
          px-4 py-2 bg-[var(--surface)] border border-[var(--primary)]/20
          rounded-2xl shadow-sm transition hover:bg-[var(--primary)]/10 hover:border-[var(--primary)]/60
          focus:ring-2 focus:ring-[var(--primary)] focus:outline-none
          min-w-[85px]
        `}
        tabIndex={-1}
      >
        {isAI && (
          <span className="absolute top-0 right-0 text-[10px] px-1 py-0.5 rounded bg-[var(--primary)] text-white shadow -translate-y-2 translate-x-2">
            AI
          </span>
        )}
        <span className={`w-8 h-8 flex items-center justify-center rounded-xl text-white shadow text-xl ${iconColor} mb-1`}>
          {icon}
        </span>
        <span className="font-serif font-bold text-[var(--primary)] text-xs">
          {title}
        </span>
      </Link>
      {hovered && description && (
        <div className="z-40 absolute left-1/2 -translate-x-1/2 top-[105%] w-52 text-center bg-[var(--surface)] border border-[var(--primary)]/20 rounded-lg px-3 py-2 text-[var(--primary)] text-xs shadow-xl animate-fade-in pointer-events-none">
          {description}
        </div>
      )}
    </div>
  );
}

// --- Main Page ---
export default function MainPage() {
  const { user } = useAuth();
  const { t } = useTranslation();

  // App cards
  const cards = [
    {
      title: t("sessions"),
      description: t("sessions_desc"),
      icon: <ScrollText className="w-6 h-6" />,
      href: "/user_table",
      show: !!user,
      group: "game",
      iconColor: "bg-green-400",
    },
    {
      title: t("notes"),
      description: t("notes_desc"),
      icon: <StickyNote className="w-6 h-6" />,
      href: "/user_notes",
      show: !!user,
      group: "game",
      iconColor: "bg-rose-400",
    },
    {
      title: t("worlds"),
      description: t("explore_worlds_desc"),
      icon: <Globe className="w-6 h-6" />,
      href: "/worlds",
      show: !!user,
      group: "worldcraft",
      iconColor: "bg-purple-400",
    },
    {
      title: t("all_pages"),
      description: t("all_pages_desc"),
      icon: <BookOpenText className="w-6 h-6" />,
      href: "/all_pages",
      show: user && ["writer", "system admin"].includes(user.role),
      group: "worldcraft",
      iconColor: "bg-yellow-400",
    },
    {
      title: t("libraries"),
      description: t("libraries_desc"),
      icon: <Library className="w-6 h-6" />,
      href: "/library",
      show: !!user,
      group: "worldcraft",
      iconColor: "bg-orange-400",
    },
    {
      title: t("ai_world_elders"),
      description: t("desc_agent_conversational"),
      icon: <Users className="w-6 h-6" />,
      href: "/elders",
      show: !!user,
      isAI: true,
      group: "ai",
      iconColor: "bg-blue-400",
    },
    {
      title: t("ai_system_specialists"),
      description: t("desc_agent_specialist"),
      icon: <Sparkles className="w-6 h-6" />,
      href: "/ai_specialist",
      show: !!user,
      isAI: true,
      group: "ai",
      iconColor: "bg-indigo-400",
    },
    {
      title: t("ai_page_writers"),
      description: t("desc_agent_writer"),
      icon: <Bot className="w-6 h-6" />,
      href: "/agent_writer",
      show: user && ["writer", "system admin"].includes(user.role),
      isAI: true,
      group: "ai",
      iconColor: "bg-sky-400",
    },
    {
      title: t("ai_adventure_novelists"),
      description: t("desc_agent_novelist"),
      icon: <PenLine className="w-6 h-6" />,
      href: "/ai_novelist",
      show: user && ["writer", "system admin"].includes(user.role),
      isAI: true,
      group: "ai",
      iconColor: "bg-pink-400",
    },
    {
      title: t("world_builder"),
      description: t("world_builder_desc"),
      icon: <Hammer className="w-6 h-6" />,
      href: "/world_builder",
      show: user && user.role === "system admin",
      group: "system",
      iconColor: "bg-yellow-500",
    },
    {
      title: t("system_settings"),
      description: t("system_settings_desc"),
      icon: <Settings className="w-6 h-6" />,
      href: "/system_settings",
      show: user && user.role === "system admin",
      group: "system",
      iconColor: "bg-gray-400",
    },
  ];

  // Row definitions and background shades
  const groupedRows = [
    { key: "game", label: t("group_game_sessions"), bg: "from-green-50 to-white" },
    { key: "worldcraft", label: t("group_worldcraft"), bg: "from-purple-50 to-white" },
    { key: "ai", label: t("group_ai_advisors"), bg: "from-blue-50 to-white" },
    { key: "system", label: t("group_system_deck"), bg: "from-gray-50 to-white" },
  ];

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full px-2 sm:px-6 py-8 flex flex-col gap-7">
          <Suspense fallback={null}>
            <ErrorBannerClient />
          </Suspense>
          {/* Hero */}
          <div className="text-center mb-1">
            <h1 className="text-3xl md:text-4xl font-serif font-bold text-[var(--primary)] tracking-tight flex justify-center items-center gap-2">
              <span role="img" aria-label="compass">🧭</span> Choose Your Path
            </h1>
            <p className="text-[var(--foreground)]/80 text-base mt-1">
              Welcome to your world-building portal. Where would you like to begin?
            </p>
          </div>
          {/* Grouped rows */}
          {groupedRows.map((group) => {
            const groupCards = cards.filter((c) => c.group === group.key && c.show);
            if (groupCards.length === 0) return null;
            return (
              <div
                key={group.key}
                className={`rounded-2xl shadow-inner px-2 sm:px-6 py-4 transition group
                  bg-gradient-to-br ${group.bg}
                `}
              >
                <div className="flex items-center gap-2 mb-3">
                  <span className="w-2 h-2 rounded-full bg-[var(--primary)]/60" />
                  <span className="text-lg font-bold font-serif text-[var(--primary)] tracking-wide">
                    {group.label}
                  </span>
                </div>
                <div className="flex flex-wrap gap-3 items-end justify-start">
                  {groupCards.map((c, i) => (
                    <DashGlyph key={c.title} {...c} />
                  ))}
                </div>
              </div>
            );
          })}
          {/* Tooltip Animation CSS */}
          <style jsx global>{`
            @keyframes fade-in {
              from { opacity: 0; transform: translateY(10px);}
              to { opacity: 1; transform: translateY(0);}
            }
            .animate-fade-in { animation: fade-in 0.2s; }
          `}</style>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
