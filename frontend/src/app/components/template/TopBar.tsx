"use client";
import { useAuth } from "../auth/AuthProvider";
import Link from "next/link";
import ThemeToggle from "../ui/ThemeToggle";
import Image from "next/image";
import { FaSearch, FaBookmark, FaSignOutAlt } from "react-icons/fa";
import { useEffect, useRef, useState } from "react";
import { usePageSearch } from "@/app/lib/usePageSearch";
import { getGameWorlds } from "@/app/lib/gameworldsAPI";
import { MdPublic } from "react-icons/md";
import { RiFile3Line } from "react-icons/ri";
import { useRouter } from "next/navigation";
import LanguageSwitcher from "../LanguageSwitcher";
import { useTranslation } from "../../hooks/useTranslation";

export default function TopBar() {
  const { user, logout, token } = useAuth();
  const { t } = useTranslation();
  const showCreatePage =
    user &&
    (user.role === "writer" ||
      user.role === "world builder" ||
      user.role === "system admin");

  // --- Search Logic ---
  const [searchValue, setSearchValue] = useState("");
  const { pages = [], isLoading } = usePageSearch(searchValue);
  const [searchOpen, setSearchOpen] = useState(false);
  const [worldsMap, setWorldsMap] = useState({});
  const [worldsLoaded, setWorldsLoaded] = useState(false);
  const searchInputRef = useRef(null);
  const router = useRouter();

  // Load all worlds for logos
  useEffect(() => {
    if (!token || worldsLoaded) return;
    getGameWorlds(token)
      .then((allWorlds) => {
        const byId = {};
        allWorlds.forEach((w) => { byId[w.id] = w; });
        setWorldsMap(byId);
        setWorldsLoaded(true);
      })
      .catch(() => setWorldsLoaded(true));
  }, [token, worldsLoaded]);

  // Filter pages on search
  const searchResults = searchValue.length < 2 ? [] : pages.slice(0, 10);

  // Keyboard nav
  const [selectedIdx, setSelectedIdx] = useState(0);
  useEffect(() => { setSelectedIdx(0); }, [searchValue, searchOpen]);

  // Click outside to close dropdown
  useEffect(() => {
    function handleClick(e) {
      if (
        searchInputRef.current &&
        !searchInputRef.current.contains(e.target)
      ) {
        setSearchOpen(false);
      }
    }
    if (searchOpen) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [searchOpen]);

  // Handle selecting a result
  function handleSelect(page) {
    setSearchOpen(false);
    setSearchValue("");
    setSelectedIdx(0);
    if (page.world_id || page.gameworld_id) {
      // Path: /worlds/{world.id}/concept/{concept.id}/page/{page.id}
      router.push(
        `/worlds/${page.world_id || page.gameworld_id}/concept/${page.concept_id}/page/${page.id}`
      );
    }
  }

  return (
    <header
      className="
        w-full fixed top-0 left-0 z-30
        flex items-center justify-between
        px-3 sm:px-8
        h-[64px] min-h-[64px]
        border-b border-[var(--border)]
        bg-[var(--topbar-bg)]
        text-[var(--topbar-fg)]
        shadow-sm
        transition-colors
      "
    >
      {/* Left: Logo + Brand */}
      <Link href="/worlds" className="flex items-center gap-2 group select-none">
        <Image
          src="/images/logo.svg"
          alt="Shrecknet logo"
          width={100}
          height={100}
          className="drop-shadow-lg transition-transform group-hover:scale-105"
          priority
        />
      </Link>

      {/* Center: Search */}
      <div className="hidden md:flex flex-1 justify-center items-center relative">
        <div className="relative w-full max-w-xl" ref={searchInputRef}>
          <input
            className="w-full px-4 pl-11 py-2 rounded-xl bg-[var(--surface-variant)] text-[var(--foreground)]
              border border-[var(--primary)]/70 shadow-none focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] focus:outline-none
              placeholder:text-[var(--primary)]/70 text-base transition
              font-semibold"
            placeholder={t("search_placeholder")}
            value={searchValue}
            onFocus={() => setSearchOpen(true)}
            onChange={(e) => {
              setSearchValue(e.target.value);
              setSearchOpen(true);
            }}
            onKeyDown={(e) => {
              if (!searchResults.length) return;
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setSelectedIdx((i) => Math.min(i + 1, searchResults.length - 1));
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setSelectedIdx((i) => Math.max(i - 1, 0));
              }
              if (e.key === "Enter") {
                handleSelect(searchResults[selectedIdx]);
              }
            }}
            style={{
              minWidth: 220,
              maxWidth: 460,
              fontWeight: 500,
              letterSpacing: ".01em",
            }}
            aria-label={t("search_placeholder")}
          />
          <FaSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-lg text-[var(--primary)]/70" />
          {searchOpen && searchValue.length > 1 && (
            <div
              className="absolute z-[9999] mt-2 w-full bg-[var(--surface)] border border-[var(--primary)]/20 rounded-xl shadow-xl"
              style={{
                minWidth: 220,
                maxWidth: 460,
                left: 0,
                right: 0,
                maxHeight: 340,
                overflowY: "auto",
              }}
            >
              {isLoading ? (
                <div className="py-4 px-5 text-sm text-[var(--primary)]/80">{t("loading")}</div>
              ) : searchResults.length === 0 ? (
                <div className="py-4 px-5 text-sm text-[var(--primary)]/70">{t("no_results_found")}</div>
              ) : (
                searchResults.map((page, i) => {
                  const world = worldsMap[page.world_id || page.gameworld_id];
                  return (
                    <button
                      key={page.id}
                      onClick={() => handleSelect(page)}
                      className={`
                        w-full flex items-center gap-2 px-4 py-2 rounded-lg
                        transition
                        ${i === selectedIdx
                          ? "bg-[var(--primary)]/15"
                          : "hover:bg-[var(--primary)]/10"}
                        text-left
                        focus:outline-none
                      `}
                      tabIndex={0}
                    >
                      {/* World Logo */}
                      {world?.logo ? (
                        <Image
                          src={world.logo}
                          alt={world.name || "World"}
                          className="w-7 h-7 rounded-full border border-[var(--primary)] bg-white object-cover"
                          style={{ minWidth: 28, minHeight: 28 }}
                          width={400}
                          height={400}
                        />
                      ) : (
                        <MdPublic className="w-6 h-6 text-[var(--primary)]/80" />
                      )}
                      {/* Page Icon */}
                      <RiFile3Line className="w-5 h-5 text-[var(--primary)]/70" />
                      {/* Page Name */}
                      <span className="font-semibold text-[var(--primary)] truncate">
                        {page.name}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          )}
        </div>
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-3 ml-4">
        {showCreatePage && (
          <Link
            href="/create_page"
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-[var(--primary)] text-[var(--primary-foreground)]
              border border-[var(--accent)] font-semibold shadow-sm hover:bg-[var(--accent)] hover:text-[var(--primary)]
              hover:shadow-md transition"
            tabIndex={0}
            style={{
              fontSize: "1rem",
              letterSpacing: ".01em",
              minHeight: "44px",
            }}
          >
            <FaBookmark className="text-lg" />
            <span className="hidden sm:inline">{t("create_page")}</span>
          </Link>
        )}
        <LanguageSwitcher className="w-24" />
        <ThemeToggle />
        <button
          onClick={() => {
            logout();
            window.location.href = "/";
          }}
          className="px-3 py-2 rounded-xl bg-transparent hover:bg-[var(--primary)] text-[var(--primary)] hover:text-[var(--primary-foreground)] border border-[var(--primary)] shadow-none font-semibold flex items-center gap-1 transition"
        >
          <FaSignOutAlt className="text-lg" />
          <span className="hidden sm:inline">{t("logout")}</span>
        </button>
      </div>
    </header>
  );
}
