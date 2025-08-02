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
import { Suspense } from "react";

// --- Fix: Client-only ErrorBanner ---
import { useSearchParams } from "next/navigation";
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

function HomeCard({ href, icon, title, description, isAI = false, color, iconColor }) {
  return (
    <Link
      href={href}
      className={`group relative overflow-hidden rounded-3xl border border-[var(--border)] bg-[var(--surface)] p-6 shadow-md hover:shadow-xl hover:border-[var(--primary)]/60 transition duration-300 ${color} flex flex-col items-center text-center`}
    >
      {/* Glow Ring */}
      <div className="absolute -top-5 -right-5 w-24 h-24 rounded-full bg-[var(--primary)]/20 blur-2xl pointer-events-none z-0 transition-all duration-500 group-hover:opacity-60" />

      {/* AI Badge */}
      {isAI && (
        <div className="absolute top-3 right-3 z-10 text-xs bg-[var(--primary)] text-white px-2 py-0.5 rounded-full shadow">
          AI
        </div>
      )}

      {/* Icon + Title */}
      <div className="relative z-10 flex flex-col items-center gap-4 mb-4">
        <div className={`w-16 h-16 rounded-2xl flex items-center justify-center text-white shadow-md ${iconColor}`}>
          {icon}
        </div>
        <div>
          <h2 className="text-lg font-serif font-bold text-[var(--primary)] leading-tight">
            {title}
          </h2>
          {isAI && (
            <p className="text-[10px] text-[var(--foreground)]/60 italic group-hover:text-[var(--primary)] transition">
              Empowered by ChatGPT
            </p>
          )}
        </div>
      </div>

      {/* Description */}
      <p className="text-sm text-[var(--foreground)]/80 relative z-10">
        {description}
      </p>
    </Link>
  );
}

export default function MainPage() {
  const { user } = useAuth();
  const { t } = useTranslation();

  const cards = [
    {
      title: t("sessions"),
      description: t("sessions_desc"),
      icon: <ScrollText className="w-6 h-6" />,
      href: "/tables",
      show: !!user,
      group: "game",
      color: "bg-gradient-to-br from-green-50 to-transparent",
      iconColor: "bg-green-400",
    },
    {
      title: t("notes"),
      description: t("notes_desc"),
      icon: <StickyNote className="w-6 h-6" />,
      href: "/user_notes",
      show: !!user,
      group: "game",
      color: "bg-gradient-to-br from-rose-50 to-transparent",
      iconColor: "bg-rose-400",
    },
    {
      title: t("worlds"),
      description: t("explore_worlds_desc"),
      icon: <Globe className="w-6 h-6" />,
      href: "/worlds",
      show: !!user,
      group: "worldcraft",
      color: "bg-gradient-to-br from-purple-50 to-transparent",
      iconColor: "bg-purple-400",
    },
    {
      title: t("all_pages"),
      description: t("all_pages_desc"),
      icon: <BookOpenText className="w-6 h-6" />,
      href: "/all_pages",
      show: user && ["writer", "system admin"].includes(user.role),
      group: "worldcraft",
      color: "bg-gradient-to-br from-yellow-50 to-transparent",
      iconColor: "bg-yellow-400",
    },
    {
      title: t("libraries"),
      description: t("libraries_desc"),
      icon: <Library className="w-6 h-6" />,
      href: "/library",
      show: !!user,
      group: "worldcraft",
      color: "bg-gradient-to-br from-orange-50 to-transparent",
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
      color: "bg-gradient-to-br from-blue-50 to-transparent",
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
      color: "bg-gradient-to-br from-indigo-50 to-transparent",
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
      color: "bg-gradient-to-br from-sky-50 to-transparent",
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
      color: "bg-gradient-to-br from-pink-50 to-transparent",
      iconColor: "bg-pink-400",
    },
    {
      title: t("world_builder"),
      description: t("world_builder_desc"),
      icon: <Hammer className="w-6 h-6" />,
      href: "/world_builder",
      show: user && user.role === "system admin",
      group: "system",
      color: "bg-gradient-to-br from-yellow-100 to-transparent",
      iconColor: "bg-yellow-500",
    },
    {
      title: t("system_settings"),
      description: t("system_settings_desc"),
      icon: <Settings className="w-6 h-6" />,
      href: "/system_settings",
      show: user && user.role === "system admin",
      group: "system",
      color: "bg-gradient-to-br from-gray-100 to-transparent",
      iconColor: "bg-gray-400",
    },
  ];

  const grouped = {
    game: {
      label: t("group_game_sessions"),
      color: "",
    },
    worldcraft: {
      label: t("group_worldcraft"),
      color: "",
    },
    ai: {
      label: t("group_ai_advisors"),
      color: "",
    },
    system: {
      label: t("group_system_deck"),
      color: "",
    },
  };

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full px-2 sm:px-6 py-8 flex flex-col gap-6">
          <Suspense fallback={null}>
            <ErrorBannerClient />
          </Suspense>

          {/* Hero */}
          <div className="text-center mb-6">
            <h1 className="text-3xl md:text-4xl font-serif font-bold text-[var(--primary)] tracking-tight">
              🧭 Choose Your Path
            </h1>
            <p className="text-[var(--foreground)]/80 text-base mt-1">
              Welcome to your world-building portal. Where would you like to begin?
            </p>
          </div>

          {/* Sections */}
          {Object.entries(grouped).map(([groupKey, { label }]) => (
            <div key={groupKey} className="space-y-4">
              <h2 className="text-xl font-serif font-bold text-[var(--primary)] flex items-center gap-2">
                {label}
              </h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                {cards.filter((c) => c.group === groupKey && c.show).map((c) => (
                  <HomeCard key={c.title} {...c} />
                ))}
              </div>
            </div>
          ))}
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
