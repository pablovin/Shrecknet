"use client";
import { useState, useEffect } from "react";
import { usePathname } from "next/navigation";
import { useAuth } from "../auth/AuthProvider";
import UserModal from "../user_management/User_Modal";
import Image from "next/image";
import Link from "next/link";
import { useTranslation } from "../../hooks/useTranslation";
import NewsDialog from "../news/NewsDialog";
import NotificationsIcon from "@mui/icons-material/NotificationsRounded";
import EditIcon from "@mui/icons-material/EditRounded";
import {
  BookOpenText,
  FileText,
  PenLine,
  Sparkles,
  ScrollText,
  Library as LibraryIcon,
  Globe,
  Hammer,
  Settings,
  StickyNote,
  Users,
} from "lucide-react";
import { getNews, markNewsSeen } from "../../lib/newsAPI";

const MENU_GROUPS = [
  {
    label: "Game Sessions",
    items: [
      {
        key: "sessions",
        label: "Sessions",
        icon: <ScrollText className="w-5 h-5" />,
        href: "/user_table",
      },
      {
        key: "notes",
        label: "Notes",
        icon: <StickyNote className="w-5 h-5" />,
        href: "/user_notes",
      },
    ],
  },
  {
    label: "Worldcraft",
    items: [
      {
        key: "worlds",
        label: "Worlds",
        icon: <Globe className="w-5 h-5" />,
        href: "/worlds",
      },
      {
        key: "library",
        label: "Library",
        icon: <LibraryIcon className="w-5 h-5" />,
        href: "/library",
      },
      {
        key: "all_pages",
        label: "All Pages",
        icon: <BookOpenText className="w-5 h-5" />,
        href: "/all_pages",
        show: (user) => user && ["writer", "system admin"].includes(user.role),
      },
    ],
  },
  {
    label: "AI Advisors",
    items: [
      {
        key: "elders",
        label: "Elders",
        icon: <Users className="w-5 h-5" />,
        href: "/elders",
        badge: "AI",
      },
      {
        key: "specialists",
        label: "Specialists",
        icon: <Sparkles className="w-5 h-5" />,
        href: "/ai_specialist",
        badge: "AI",
      },
      {
        key: "writers",
        label: "Writers",
        icon: <BookOpenText className="w-5 h-5" />,
        href: "/agent_writer",
        badge: "AI",
        show: (user) => user && ["writer", "system admin"].includes(user.role),
      },
      {
        key: "novelists",
        label: "Novelists",
        icon: <PenLine className="w-5 h-5" />,
        href: "/ai_novelist",
        badge: "AI",
        show: (user) => user && ["writer", "system admin"].includes(user.role),
      },
    ],
  },
  {
    label: "System Deck",
    items: [
      {
        key: "world_builder",
        label: "World Builder",
        icon: <Hammer className="w-5 h-5" />,
        href: "/world_builder",
        show: (user) =>
          user && ["world builder", "system admin"].includes(user.role),
      },
      {
        key: "system_settings",
        label: "System Settings",
        icon: <Settings className="w-5 h-5" />,
        href: "/system_settings",
        show: (user) => user && user.role === "system admin",
      },
    ],
  },
];

export default function Sidebar({
  mobileOpen = false,
  setMobileOpen = () => {},
}) {
  const { user, token, isLoading: authLoading, refreshUser } = useAuth();
  const { t } = useTranslation();
  const [profileModalOpen, setProfileModalOpen] = useState(false);
  const [profileSuccess, setProfileSuccess] = useState("");
  const [profileError, setProfileError] = useState("");
  const [newsOpen, setNewsOpen] = useState(false);
  const [newsItems, setNewsItems] = useState([]);
  const pathname = usePathname();

  // Fetch news on mount
  useEffect(() => {
    if (!token) return;
    getNews(token)
      .then((items) => {
        setNewsItems(items);
        const unseen = items.filter((n) => !n.seen);
        const today = new Date().toDateString();
        const last =
          typeof window !== "undefined"
            ? localStorage.getItem("news_last_seen")
            : null;
        if (last !== today && unseen.length > 0) {
          setNewsOpen(true);
          localStorage.setItem("news_last_seen", today);
          unseen.forEach((n) => markNewsSeen(n.id, token));
          setNewsItems(items.map((n) => ({ ...n, seen: true })));
        }
      })
      .catch(() => {});
  }, [token]);

  // Prevent background scroll when sidebar is open (mobile)
  useEffect(() => {
    if (mobileOpen) document.body.style.overflow = "hidden";
    else document.body.style.overflow = "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  // Mark all news seen on open
  function handleOpenNews() {
    setNewsOpen(true);
    const unseen = newsItems.filter((n) => !n.seen);
    if (unseen.length > 0) {
      unseen.forEach((n) => markNewsSeen(n.id, token));
      setNewsItems(newsItems.map((n) => ({ ...n, seen: true })));
      if (typeof window !== "undefined") {
        localStorage.setItem("news_last_seen", new Date().toDateString());
      }
    }
  }

  function isActive(href) {
    return !href.startsWith("http") && pathname.startsWith(href);
  }

  if (authLoading || !token) return null;

  return (
    <>
      {/* Overlay for mobile */}
      <div
        className={`fixed inset-0 z-40 bg-black/50 backdrop-blur-sm transition-opacity duration-300
        ${mobileOpen ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"}
        md:hidden`}
        onClick={() => setMobileOpen(false)}
        aria-hidden="true"
      />
      {/* Sidebar */}
      <aside
        className={`
          fixed left-0 top-0 z-50 h-full w-[90vw] max-w-[320px] md:w-64
          bg-gradient-to-b from-[var(--sidebar-bg)] to-white border-r border-[var(--border)]
          shadow-xl flex flex-col overflow-y-auto transition-transform duration-300 ease-in-out
          ${mobileOpen ? "translate-x-0" : "-translate-x-full"}
          md:translate-x-0 md:relative md:block
        `}
        style={{ minWidth: 120 }}
      >
        {/* Close button for mobile */}
        <div className="flex md:hidden justify-end px-4 pt-4">
          <button
            className="text-3xl text-[var(--primary)]"
            onClick={() => setMobileOpen(false)}
            aria-label="Close menu"
          >
            &times;
          </button>
        </div>
        {/* Logo */}
        <div className="flex items-center justify-center h-20 py-3 border-b border-[var(--border)] bg-[var(--sidebar-bg)]">
          <Image
            src="/images/logo_dark.png"
            alt="Shrecknet logo"
            width={70}
            height={70}
            className="w-16 h-16 object-contain drop-shadow-lg"
            priority
          />
        </div>
        {/* ----------- User Profile FIRST ----------- */}
        <div className="flex flex-col items-center gap-2 pt-4 pb-2 border-b border-[var(--border)] bg-transparent w-full relative">
          <div className="relative flex flex-col items-center w-full">
            {/* Avatar (80% of sidebar width) */}
            <div className="relative flex items-center justify-center w-[80%] mx-auto aspect-square">
              <Image
                src={user?.image_url || "/images/avatars/default.png"}
                alt="avatar"
                width={256} // For clarity, you might want a higher src
                height={256}
                className="object-cover w-full h-full rounded-3xl border-4 border-[var(--primary)] shadow-lg"
              />
              {/* Edit Button */}
              <button
                className="absolute bottom-2 right-2 p-2 rounded-full bg-white border shadow-lg hover:bg-[var(--primary)]/10 transition"
                aria-label={t("personalize")}
                onClick={() => setProfileModalOpen(true)}
                style={{ boxShadow: "0 2px 10px rgba(80,0,130,.13)" }}
              >
                <EditIcon style={{ fontSize: 26, color: "var(--primary)" }} />
              </button>
            </div>

            {/* Notification Bar */}
            <button
              onClick={handleOpenNews}
              className={`
                w-[80%] mx-auto mt-3 flex items-center justify-center gap-2 px-3 py-2 rounded-lg font-semibold
                transition-all
                ${
                  newsItems.some((n) => !n.seen)
                    ? "bg-red-600 text-white shadow-lg animate-pulse"
                    : "bg-[var(--surface)] text-[var(--primary)] border"
                }
              `}
              aria-label={t("news")}
              style={{ boxShadow: "0 1px 6px rgba(80,0,130,.09)" }}
            >
              <NotificationsIcon fontSize="medium" />
              <span>
                {t("Notifications")}
                {newsItems.some((n) => !n.seen) && (
                  <span className="ml-2 inline-block w-2 h-2 bg-white rounded-full shadow" />
                )}
              </span>
            </button>

            {/* Nickname */}
            <div className="font-bold text-[var(--primary)] text-lg capitalize text-center mt-2">
              {user?.nickname || t("hi")}
            </div>
          </div>

          {profileSuccess && (
            <div className="w-full text-center bg-green-100 text-green-700 px-2 py-1 rounded text-xs">
              {profileSuccess}
            </div>
          )}
          {profileError && (
            <div className="w-full text-center bg-red-100 text-red-700 px-2 py-1 rounded text-xs">
              {profileError}
            </div>
          )}
        </div>
        {/* ------------------------------------------ */}
        {/* ----------- Main Link at the Top ----------- */}
        <Link
          href="/main"
          className={`
            flex items-center gap-3 px-4 py-3 mt-2 mb-2 rounded-2xl font-semibold
            text-base transition
            ${
              pathname.startsWith("/main")
                ? "bg-[var(--primary)]/10 text-[var(--primary)] shadow"
                : "text-[var(--foreground)] hover:bg-[var(--primary)]/5"
            }
          `}
          style={{
            borderLeft: pathname.startsWith("/main")
              ? "5px solid var(--primary)"
              : "5px solid transparent",
          }}
          onClick={() => setMobileOpen(false)}
        >
          <span className="text-2xl">
            <svg width="20" height="20" fill="none">
              <path
                d="M10 3L3 10h2v7h3v-4h2v4h3v-7h2L10 3z"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span>Main</span>
        </Link>
        {/* ------------------------------------------ */}
        {/* Menu Groups */}
        <nav className="flex-1 flex flex-col gap-6 py-6">
          {MENU_GROUPS.map((group) => (
            <div key={group.label}>
              <div className="px-6 text-xs uppercase font-bold tracking-wider text-[var(--primary)]/80 mb-2">
                {group.label}
              </div>
              <div className="flex flex-col gap-2">
                {group.items
                  .filter((item) => !item.show || item.show(user))
                  .map((item) => (
                    <Link
                      key={item.key}
                      href={item.href}
                      className={`
                        flex items-center gap-3 px-4 py-3 rounded-2xl font-semibold
                        text-base group transition
                        ${
                          isActive(item.href)
                            ? "bg-[var(--primary)]/10 text-[var(--primary)] shadow"
                            : "text-[var(--foreground)] hover:bg-[var(--primary)]/5"
                        }
                      `}
                      style={{
                        borderLeft: isActive(item.href)
                          ? "5px solid var(--primary)"
                          : "5px solid transparent",
                      }}
                      onClick={() => setMobileOpen(false)}
                    >
                      <span className="text-2xl">{item.icon}</span>
                      <span className="flex items-center gap-1">
                        {item.label}
                        {item.badge && (
                          <span className="ml-1 text-[10px] font-bold border rounded px-1 border-[var(--primary)] text-[var(--primary)]">
                            {item.badge}
                          </span>
                        )}
                      </span>
                    </Link>
                  ))}
              </div>
            </div>
          ))}
        </nav>
        {/* Profile Modal (fullscreen, center, not just in sidebar) */}
        {profileModalOpen && user && (
          <div className="fixed inset-0 z-[9999] bg-black/50 flex items-center justify-center">
            <div className="bg-white rounded-3xl shadow-2xl p-4 w-full max-w-md mx-auto relative">
              <UserModal
                user={user}
                onClose={() => setProfileModalOpen(false)}
                onSave={async () => {
                  if (typeof refreshUser === "function") await refreshUser();
                  setProfileModalOpen(false);
                  setProfileSuccess(t("profile_updated_success"));
                  setTimeout(() => setProfileSuccess(""), 2000);
                }}
                onDelete={null}
                isProfile={true}
                setError={(msg) => {
                  setProfileError(msg);
                  setTimeout(() => setProfileError(""), 2000);
                }}
              />
              <button
                className="absolute top-2 right-4 text-2xl text-[var(--primary)]"
                onClick={() => setProfileModalOpen(false)}
                aria-label="Close"
              >
                &times;
              </button>
            </div>
          </div>
        )}
      </aside>
      {/* News Dialog */}
      <NewsDialog
        open={newsOpen}
        onClose={() => setNewsOpen(false)}
        news={newsItems}
      />
    </>
  );
}
