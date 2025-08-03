"use client";
import { useAuth } from "../auth/AuthProvider";
import Link from "next/link";
import ThemeToggle from "../ui/ThemeToggle";
import Image from "next/image";
import { FaSearch, FaBookmark, FaSignOutAlt, FaBars } from "react-icons/fa";
import { useEffect, useRef, useState } from "react";
import { usePageSearch } from "@/app/lib/usePageSearch";
import { getGameWorlds } from "@/app/lib/gameworldsAPI";
import { MdPublic } from "react-icons/md";
import { RiFile3Line } from "react-icons/ri";
import { useRouter } from "next/navigation";
import LanguageSwitcher from "../LanguageSwitcher";
import { useTranslation } from "../../hooks/useTranslation";

export default function TopBar({ onSidebarToggle }) {
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
      router.push(
        `/worlds/${page.world_id || page.gameworld_id}/concept/${page.concept_id}/page/${page.id}`
      );
    }
  }

  // Keyboard shortcut: `/` to focus search
  useEffect(() => {
    function handleKeyDown(e) {
      if ((e.key === "/" || (e.ctrlKey && e.key.toLowerCase() === "k")) && !searchOpen) {
        setSearchOpen(true);
        setTimeout(() => {
          if (searchInputRef.current) {
            const input = searchInputRef.current.querySelector("input");
            if (input) input.focus();
          }
        }, 50);
        e.preventDefault();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [searchOpen]);

  return (
    <header
      className="
        fixed top-0 left-0 z-30 w-full h-20 bg-[var(--topbar-bg)]/80 backdrop-blur-xl
        shadow-[0_4px_32px_-8px_rgba(130,88,220,0.10)] border-b border-[var(--border)]
        flex items-center px-2 sm:px-8 transition-colors
      "
      style={{ minHeight: 64 }}
    >
      {/* Sidebar trigger (mobile only) */}
      <button
        className="md:hidden p-2 mr-2 rounded-full bg-[var(--primary)] text-white shadow-lg"
        onClick={onSidebarToggle}
        aria-label="Open menu"
      >
        <FaBars size={22} />
      </button>
      {/* Logo and Brand */}
      <Link href="/main" className="flex items-center gap-2 select-none group mr-4">
        <Image
          src="/images/logo.svg"
          alt="Shrecknet logo"
          width={44}
          height={44}
          className="rounded-full shadow-sm transition-transform group-hover:scale-105"
          priority
        />
        <span className="hidden md:block font-serif font-bold text-[var(--primary)] text-2xl tracking-tight drop-shadow-sm ml-1">
          Shrecknet
        </span>
      </Link>

      {/* Centered Search Bar */}
      <div className="flex-1 flex justify-center">
        <div className="relative w-full max-w-xl" ref={searchInputRef}>
          <input
            className="
              w-full px-5 pl-12 py-3 rounded-2xl bg-white/60 backdrop-blur
              text-[var(--foreground)] border border-[var(--primary)]/70 shadow-md
              focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)] focus:outline-none
              placeholder:text-[var(--primary)]/70 text-base font-semibold transition
              "
            placeholder={t("search_placeholder") + " (/)"}
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
          <FaSearch className="absolute left-4 top-1/2 -translate-y-1/2 text-xl text-[var(--primary)]/70 pointer-events-none" />
          {searchOpen && searchValue.length > 1 && (
            <div
              className="absolute z-[9999] mt-2 w-full bg-white/90 backdrop-blur border border-[var(--primary)]/15 rounded-2xl shadow-2xl"
              style={{
                minWidth: 220,
                maxWidth: 460,
                left: 0,
                right: 0,
                maxHeight: 360,
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
                        w-full flex items-center gap-2 px-4 py-2 rounded-xl
                        transition
                        ${i === selectedIdx
                          ? "bg-[var(--primary)]/15"
                          : "hover:bg-[var(--primary)]/10"}
                        text-left focus:outline-none
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

      {/* Actions */}
      <div className="
        flex items-center gap-2 md:gap-3
        rounded-2xl bg-white/40 backdrop-blur border border-[var(--border)]
        shadow px-2 py-1 ml-2 md:ml-4
        ">
        {showCreatePage && (
          <Link
            href="/create_page"
            className="
              flex items-center gap-2 px-3 py-2 rounded-xl
              bg-[var(--primary)] text-white shadow hover:bg-[var(--accent)] transition text-base font-bold
            "
            tabIndex={0}
            style={{ fontSize: "1rem", letterSpacing: ".01em", minHeight: "44px" }}
          >
            <FaBookmark className="text-lg" />
            <span className="hidden sm:inline">{t("create_page")}</span>
          </Link>
        )}
        <LanguageSwitcher className="w-20 md:w-28" />
        <ThemeToggle />
        <button
          onClick={() => {
            logout();
            window.location.href = "/";
          }}
          className="
            px-2 py-2 rounded-xl bg-transparent hover:bg-[var(--primary)]
            text-[var(--primary)] hover:text-white border border-[var(--primary)] shadow-none
            font-semibold flex items-center gap-1 transition
          "
        >
          <FaSignOutAlt className="text-lg" />
          <span className="hidden sm:inline">{t("logout")}</span>
        </button>
      </div>
    </header>
  );
}
