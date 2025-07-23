"use client";
import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { Sparkles } from "lucide-react";
import { useTranslation } from "@/app/hooks/useTranslation";

export interface PageTab {
  value: string;
  label: string;
  icon?: LucideIcon;
  ai?: boolean;
}

export default function PageTabMenu({
  activeTab,
  onTabChange,
  tabs,
}: {
  activeTab: string;
  onTabChange: (tab: string) => void;
  tabs: PageTab[];
}) {
  const { t } = useTranslation();
  return (
    <nav className="relative flex w-full bg-[var(--surface-variant)] p-1 rounded-xl border border-[var(--primary)]/10 text-xs">
      {tabs.map((tab) => {
        const isActive = activeTab === tab.value;
        const Icon = tab.icon;
        return (
          <div key={tab.value} className="flex-1 relative">
            {isActive && (
              <motion.div
                layoutId="pageTabHighlight"
                className="absolute inset-0 rounded-md bg-[var(--primary)] z-0"
                transition={{ type: "spring", stiffness: 350, damping: 30 }}
              />
            )}
            <button
              className={`relative w-full z-10 flex items-center justify-center gap-1 font-semibold px-2 py-1 rounded-md transition-colors ${
                isActive
                  ? "text-[var(--primary-foreground)]"
                  : "text-[var(--primary)] hover:bg-[var(--primary)]/10"
              }`}
              style={{ letterSpacing: ".02em" }}
              onClick={() => onTabChange(tab.value)}
            >
              {Icon && (
                <span
                  className={`w-5 h-5 flex items-center justify-center rounded-md ${
                    isActive
                      ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                      : "bg-[var(--primary)]/20 text-[var(--primary)]"
                  }`}
                >
                  <Icon className="w-4 h-4" />
                </span>
              )}
              {tab.ai && (
                <Sparkles className="w-3 h-3 text-[var(--primary)]" />
              )}
              {t(tab.label as any)}
            </button>
          </div>
        );
      })}
    </nav>
  );
}
