import React, { useState, PropsWithChildren, useEffect } from "react";
import TopBar from "./template/TopBar";
import Sidebar from "./template/Sidebar";
import ChatPanel from "./template/ChatPanel";
import { FaBars } from "react-icons/fa";

export default function DashboardLayout({ children }: PropsWithChildren) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatDefaultAgentId, setChatDefaultAgentId] = useState<number | null>(null);
  const [chatDefaultInput, setChatDefaultInput] = useState<string>("");

  useEffect(() => {
    function handleOpen(e: any) {
      if (e.detail?.agentId !== undefined) setChatDefaultAgentId(e.detail.agentId);
      if (e.detail?.input !== undefined) setChatDefaultInput(e.detail.input);
      setChatOpen(true);
    }
    window.addEventListener("openChatPanel", handleOpen);
    return () => window.removeEventListener("openChatPanel", handleOpen);
  }, []);

  return (
   <div className="relative bg-[var(--background)] min-h-screen w-full flex">
      {/* Hamburger menu (mobile only) */}
      <button
        className="fixed top-5 right-5 z-50 md:hidden bg-[var(--primary)] text-white p-2 rounded-full shadow-lg"
        onClick={() => setSidebarOpen(true)}
        aria-label="Open menu"
      >
        <FaBars size={22} />
      </button>

      {/* Sidebar (hidden on mobile unless open) */}
      <Sidebar
        mobileOpen={sidebarOpen}
        setMobileOpen={setSidebarOpen}
      />

      {/* Main content */}
      <div className="flex-1 flex flex-col min-h-screen bg-[var(--background)] transition-colors duration-300 pr-0 md:pl-64">
        <TopBar onSidebarToggle={() => setSidebarOpen(true)} />
        <main className="flex-1 px-2 sm:px-6 py-6 mt-[64px] overflow-y-auto">
          {children}
        </main>
      </div>

      {/* Chat panel (right, floating) */}
      <ChatPanel
        open={chatOpen}
        onOpen={() => setChatOpen(true)}
        onClose={() => setChatOpen(false)}
        defaultAgentId={chatDefaultAgentId}
        defaultInput={chatDefaultInput}
      />
    </div>
  );
}
