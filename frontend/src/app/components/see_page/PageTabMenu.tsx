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
    <nav className="relative flex flex-wrap sm:flex-nowrap w-full gap-2 sm:gap-0 bg-[var(--surface-variant)] p-1 rounded-xl border border-[var(--primary)]/10 text-xs shadow-sm">
      {tabs.map((tab) => {
        const isActive = activeTab === tab.value;
        const Icon = tab.icon;
        return (
          <div key={tab.value} className="relative flex-1 min-w-[6rem]">
            {isActive && (
              <motion.div
                layoutId="pageTabHighlight"
                className="absolute inset-0 rounded-md bg-gradient-to-r from-[var(--primary)] to-[var(--primary)] z-0 shadow-inner"
                transition={{ type: "spring", stiffness: 350, damping: 30 }}
              />
            )}
            <button
              className={`relative w-full z-10 flex items-center justify-center gap-1 font-semibold px-3 py-2 sm:px-2 sm:py-1 rounded-md transition-colors text-sm tracking-wide ${
                isActive
                  ? "text-[var(--primary-foreground)]"
                  : "text-[var(--primary)] hover:bg-[var(--primary)]/10"
              }`}
              onClick={() => onTabChange(tab.value)}
            >
              {Icon && (
                <span
                  className={`w-5 h-5 flex items-center justify-center rounded-full border ${
                    isActive
                      ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                      : "bg-[var(--primary)]/10 text-[var(--primary)] border-[var(--primary)]/20"
                  }`}
                >
                  <Icon className="w-4 h-4" />
                </span>
              )}
              <span className="whitespace-nowrap flex items-center gap-1">
                {t(tab.label as any)}
                {tab.ai && (
                  <span className="ml-1 px-1.5 py-0.5 text-[10px] font-bold bg-[var(--primary)]/10 text-[var(--primary)] rounded-md border border-[var(--primary)]/30">AI</span>
                )}
              </span>
            </button>
          </div>
        );
      })}
    </nav>
  );
}