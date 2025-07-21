"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import { useTranslation } from "../hooks/useTranslation";
import { useSearchParams } from "next/navigation";
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
} from "lucide-react";

function HomeCard({ href, icon, title, description }) {
  return (
    <Link
      href={href}
      className="group relative overflow-hidden rounded-3xl border border-[var(--border)] bg-[var(--surface)] p-6 shadow-md hover:shadow-xl transition"
    >
      <div className="relative z-10 flex items-center gap-3 mb-3">
        <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-[var(--primary)]/10 text-[var(--primary)]">
          {icon}
        </div>
        <h2 className="text-lg font-bold text-[var(--primary)]">{title}</h2>
      </div>
      <p className="text-sm text-[var(--foreground)]/80">{description}</p>
    </Link>
  );
}

export default function MainPage() {
  const { user } = useAuth();
  const { t } = useTranslation();
  const params = useSearchParams();
  const error = params.get("error");

  const cards = [
    {
      title: t("worlds"),
      description: t("explore_worlds_desc"),
      icon: <Globe className="w-6 h-6" />,
      href: "/worlds",
      show: true,
    },
    {
      title: t("library"),
      description: t("library_desc"),
      icon: <BookOpenText className="w-6 h-6" />,
      href: "/library",
      show: true,
    },
    {
      title: t("ai_world_elders"),
      description: t("desc_agent_conversational"),
      icon: <Users className="w-6 h-6" />,
      href: "/elders",
      show: true,
    },
    {
      title: t("ai_system_specialists"),
      description: t("desc_agent_specialist"),
      icon: <Sparkles className="w-6 h-6" />,
      href: "/ai_specialist",
      show: true,
    },
    {
      title: t("ai_page_writers"),
      description: t("desc_agent_writer"),
      icon: <Bot className="w-6 h-6" />,
      href: "/agent_writer",
      show: user && ["writer", "system admin"].includes(user.role),
    },
    {
      title: t("ai_adventure_novelists"),
      description: t("desc_agent_novelist"),
      icon: <PenLine className="w-6 h-6" />,
      href: "/ai_novelist",
      show: user && ["writer", "system admin"].includes(user.role),
    },
    {
      title: t("world_builder"),
      description: "Forge new worlds and adventures.",
      icon: <Hammer className="w-6 h-6" />,
      href: "/world_builder",
      show: user && ["world builder", "system admin"].includes(user.role),
    },
    {
      title: t("system_settings"),
      description: "Manage users and system configuration.",
      icon: <Settings className="w-6 h-6" />,
      href: "/system_settings",
      show: user && user.role === "system admin",
    },
  ];

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full px-2 sm:px-6 py-8 flex flex-col gap-6">
          {error === "unauthorized" && (
            <div className="text-center bg-red-100 text-red-700 border border-red-300 px-4 py-2 rounded">
              {t("not_authorized")}
            </div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {cards.filter((c) => c.show).map((c) => (
              <HomeCard key={c.title} {...c} />
            ))}
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}

