"use client";
import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";

export interface PageTab {
  value: string;
  label: string;
  icon?: LucideIcon;
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
  return (
    <nav className="relative flex w-full bg-[var(--surface-variant)] p-1 rounded-xl border border-[var(--primary)]/10">
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
              className={`relative w-full z-10 flex items-center justify-center gap-2 text-sm font-semibold px-3 py-2 rounded-md transition-colors ${
                isActive
                  ? "text-[var(--primary-foreground)]"
                  : "text-[var(--primary)] hover:bg-[var(--primary)]/10"
              }`}
              style={{ letterSpacing: ".02em" }}
              onClick={() => onTabChange(tab.value)}
            >
              {Icon && <Icon className="w-4 h-4" />}
              {tab.label}
            </button>
          </div>
        );
      })}
    </nav>
  );
}
