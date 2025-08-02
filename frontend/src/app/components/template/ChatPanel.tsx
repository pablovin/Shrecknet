"use client";
import { FaComments } from "react-icons/fa";
import { useState, useRef, FormEvent, useEffect } from "react";
import Image from "next/image";
import { Loader2 } from "lucide-react";
import { useAgents } from "../../lib/useAgents";
import { useAuth } from "../auth/AuthProvider";
import WikiLinkHoverCard from "../editor/WikiLinkHoverCard";
import ModalContainer from "./modalContainer";
import {
  chatWithConversationalAgent,
  ChatMessage,
  getChatHistory,
  SourceLink,
} from "../../lib/agentAPI";

export default function ChatPanel({
  open,
  onOpen,
  onClose,
  defaultAgentId = null,
  defaultInput = "",
}: {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  defaultAgentId?: number | null;
  defaultInput?: string;
}) {
  const PANEL_WIDTH = 420; // px, can use % if preferred
  const BAR_WIDTH = 54; // px
  const BAR_HEIGHT = 200; // px

  // For hover effect
  const [btnHover, setBtnHover] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [allMessages, setAllMessages] = useState<ChatMessage[]>([]);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [sourceLinks, setSourceLinks] = useState<SourceLink[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const { agents, isLoading: agentsLoading } = useAgents();
  const { token } = useAuth();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const availableAgents = agents.filter(
    a => a.vector_db_update_date && a.task === "conversational"
  );
  const selectedAgent =
    selectedAgentId !== null
      ? agents.find(a => a.id === selectedAgentId)
      : null;

  useEffect(() => {
    if (!open) return;
    if (defaultAgentId !== null) setSelectedAgentId(defaultAgentId);
    if (defaultInput) setInput(defaultInput);
  }, [open, defaultAgentId, defaultInput]);

  useEffect(() => {
    if (selectedAgentId === null) return;
    getChatHistory(selectedAgentId, token || "", 10)
      .then((msgs) => {
        setAllMessages(msgs as ChatMessage[]);
        setMessages((msgs as ChatMessage[]).slice(-10));
        const last = [...(msgs as ChatMessage[])].reverse().find(m => m.role === "assistant" && m.sources);
        setSourceLinks(last?.sources || []);
        scrollToBottom();
      })
      .catch(() => {
        setMessages([]);
        setAllMessages([]);
        setSourceLinks([]);
      });
  }, [selectedAgentId, token]);

  function scrollToBottom() {
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 50);
  }

  async function handleSend(e: FormEvent) {
    e.preventDefault();
    if (!input.trim() || selectedAgentId === null) return;
    const newMsg: ChatMessage = { role: "user", content: input.trim() };
    const updatedAll = [...allMessages, newMsg];
    setAllMessages(updatedAll);
    const display = [...messages, newMsg].slice(-10);
    setMessages(display);
    setInput("");
    scrollToBottom();
    setLoading(true);
    setMessages(m => [...m, { role: "assistant", content: "" }]);
    try {
      const { answer, sources } = await chatWithConversationalAgent(
        selectedAgentId,
        updatedAll,
        token || ""
      );
      setAllMessages(m => [...m, { role: "assistant", content: answer, sources }]);
      setMessages(m => {
        const arr = [...m];
        arr[arr.length - 1] = { role: "assistant", content: answer, sources };
        return arr.slice(-10);
      });
      setSourceLinks(sources || []);
      scrollToBottom();
    } catch {
      setAllMessages(m => [...m, { role: "assistant", content: "Sorry, something went wrong." }]);
      setMessages(m => [
        ...m.slice(0, -1),
        { role: "assistant", content: "Sorry, something went wrong." },
      ].slice(-10));
      setSourceLinks([]);
      scrollToBottom();
    }
    setLoading(false);
  }

  async function handleOpenHistory() {
    if (selectedAgentId === null) return;
    try {
      const full = await getChatHistory(selectedAgentId, token || "", 1000);
      setAllMessages(full as ChatMessage[]);
      setShowHistoryModal(true);
    } catch {
      setShowHistoryModal(true);
    }
  }

  function renderMessageContent(msg: ChatMessage) {
    const lines = msg.content.split(/\n/);
    return (
      <>
        {lines.map((l, i) => (
          <span key={i}>
            {l}
            {i < lines.length - 1 && <br />}
          </span>
        ))}
      </>
    );
  }

  const panelContent = !open ? null : (
    <div className="flex-1 flex flex-col p-4 pt-6 overflow-y-auto">
      {!selectedAgent ? (
        <div className="flex flex-col gap-4">
          <h2 className="text-center text-lg font-bold text-purple-700 mb-2">
            {agentsLoading ? "Loading agents..." : "Select an Agent"}
          </h2>
          {!agentsLoading &&
            availableAgents.map((a) => (
              <button
                key={a.id}
                onClick={() => setSelectedAgentId(a.id)}
                className="flex items-center gap-3 px-3 py-2 rounded-xl bg-white/70 hover:bg-white border border-purple-200 shadow"
              >
                <Image
                  src={a.logo || "/images/default/avatars/logo.png"}
                  alt={a.name}
                  width={32}
                  height={32}
                  className="w-8 h-8 rounded object-cover border border-purple-300"
                />
                <span className="font-semibold text-purple-800">{a.name}</span>
              </button>
            ))}
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between mb-4 bg-[var(--surface-variant)] px-4 py-3 rounded-xl shadow">
            <div className="flex items-center gap-3">
              {selectedAgent?.logo && (
                <Image
                  src={selectedAgent.logo}
                  alt={selectedAgent.name}
                  width={48}
                  height={48}
                  className="w-12 h-12 rounded-full object-cover border border-purple-300"
                />
              )}
              <span className="text-xl font-bold text-[var(--primary)]">{selectedAgent?.name}</span>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={handleOpenHistory}
                className="text-sm text-purple-600 hover:underline"
              >
                See chat history
              </button>
              <button
                onClick={() => {
                  setSelectedAgentId(null);
                  setMessages([]);
                  setAllMessages([]);
                  setSourceLinks([]);
                }}
                className="text-sm text-purple-600 hover:underline"
              >
                Talk to another Elder
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto space-y-4 pb-2">
            {messages.map((m, idx) => (
              <div
                key={idx}
                className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`${
                    m.role === "user"
                      ? "bg-purple-600 text-white"
                      : "bg-white text-purple-800"
                  } rounded-xl px-3 py-2 shadow max-w-[80%] whitespace-pre-wrap`}
                >
                  {loading &&
                  idx === messages.length - 1 &&
                  m.role === "assistant" &&
                  m.content === "" ? (
                    <span className="flex items-center gap-2">
                      <Loader2 className="animate-spin w-4 h-4" />
                      Wait a second, let me read about it...
                    </span>
                  ) : (
                    renderMessageContent(m)
                  )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
          {sourceLinks.length > 0 && (
            <div className="mt-2 mb-2">
              <span className="text-sm font-semibold text-purple-700">Sources:</span>
              <ul className="text-xs flex flex-wrap gap-2 mt-1">
                {sourceLinks.map((s) => (
                  <li key={s.url}>
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline text-purple-700"
                    >
                      {s.title}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <form onSubmit={handleSend} className="mt-2 flex gap-2">
            <textarea
              className="flex-1 rounded-xl border border-purple-300 p-2 bg-white text-purple-800 placeholder-purple-400 focus:outline-none resize-y"
              rows={3}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type your message..."
              disabled={loading}
            />
            <button
              type="submit"
              className="px-4 py-2 rounded-xl bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50"
              disabled={loading || !input.trim()}
            >
              {loading ? "..." : "Send"}
            </button>
          </form>
        </>
      )}
    </div>
  );

  // Mobile: Floating FAB
  const mobileOpenButton = !open ? (
    <button
      className="fixed bottom-8 right-8 z-50 md:hidden bg-purple-700 text-white p-4 rounded-full shadow-lg"
      onClick={onOpen}
    >
      <FaComments size={24} />
    </button>
  ) : null;

  // --- DESKTOP: Sticky sliding container ---
  return (
    <>
      {/* Mobile only */}
      {mobileOpenButton}

      {/* Desktop: sticky bar and panel in a sliding container */}
      <div
        className="hidden md:flex fixed top-0 right-0 h-screen z-50"
        style={{
          width: `${BAR_WIDTH + PANEL_WIDTH}px`,
          pointerEvents: "none", // so only bar/panel are interactive
          // Slide container left when open, keep it off-screen when closed
          transform: open
            ? "translateX(0)"
            : `translateX(${BAR_WIDTH + PANEL_WIDTH - BAR_WIDTH}px)`, // keep only bar visible when closed
          transition: "transform 0.4s cubic-bezier(.55,.1,.45,.9)",
        }}
      >
        {/* --- Toggle Bar (always visible, "sticks" to panel) --- */}
        <div
          style={{
            width: `${BAR_WIDTH}px`,
            height: `${BAR_HEIGHT}px`,
            position: "relative",
            top: "calc(50vh - 100px)",
            left: 0,
            background:
              "linear-gradient(120deg,rgba(255,255,255,0.97) 80%,rgba(170,140,255,0.16) 100%)",
            borderRadius: "1.4rem 0 0 1.4rem",
            border: "2px solid #e0d7f9",
            boxShadow: "0 2px 18px 0 rgba(180,120,255,0.10)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            userSelect: "none",
            zIndex: 2,
            pointerEvents: "auto",
            transition: "background 0.2s, box-shadow 0.2s",
          }}
          className="group hover:shadow-xl hover:bg-white/95 transition-all"
          onClick={open ? onClose : onOpen}
          tabIndex={0}
          title={open ? "Close chat" : "Call the chat!"}
          onMouseEnter={() => setBtnHover(true)}
          onMouseLeave={() => setBtnHover(false)}
          onFocus={() => setBtnHover(true)}
          onBlur={() => setBtnHover(false)}
        >
          <FaComments
            className="text-purple-600 text-2xl mb-3 group-hover:scale-110 transition-transform"
          />
          <span
            className="font-semibold text-xs text-purple-800 tracking-wider"
            style={{
              writingMode: "vertical-rl",
              textOrientation: "mixed",
              letterSpacing: "0.13em",
              userSelect: "none",
              marginTop: "2px",
            }}
          >
            Call the chat!
          </span>
          <span
            style={{
              display: "block",
              position: "absolute",
              width: "56px",
              height: "56px",
              left: "-8px",
              top: "calc(50% - 28px)",
              pointerEvents: "none",
              opacity: btnHover ? 1 : 0,
              background:
                "radial-gradient(circle,rgba(180,120,255,0.19) 0,rgba(160,110,240,0.13) 60%,transparent 100%)",
              filter: "blur(1.5px)",
              transition: "opacity 0.22s cubic-bezier(.55,.1,.45,.85)",
              borderRadius: "50%",
              zIndex: 1,
            }}
          />
        </div>

        {/* --- Chat Panel --- */}
        <div
          style={{
            width: `${PANEL_WIDTH}px`,
            height: "100vh",
            background:
              "linear-gradient(120deg,rgba(255,255,255,0.96) 80%,rgba(225,195,255,0.24) 100%)",
            backdropFilter: "blur(10px)",
            boxShadow: "-4px 0 28px 0 rgba(100,80,140,0.13)",
            display: "flex",
            flexDirection: "column",
            borderLeft: "2px solid #e0d7f9",
            pointerEvents: open ? "auto" : "none",
            zIndex: 1,
            transition: "box-shadow 0.2s",
          }}
        >
          {panelContent}
        </div>
      </div>

      {/* On mobile: full-screen overlay when open */}
      <div
        className={`fixed right-0 top-0 h-full w-full z-50 flex flex-col md:hidden transition-transform duration-300 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        style={{
          background:
            "linear-gradient(120deg,rgba(255,255,255,0.97) 80%,rgba(170,140,255,0.16) 100%)",
          backdropFilter: "blur(8px)",
          boxShadow: "-4px 0 28px 0 rgba(100,80,140,0.11)",
        }}
      >
        {open && (
          <div className="flex items-center justify-between p-4 border-b border-purple-100">
            <div className="flex items-center gap-3">
              {selectedAgent?.logo && (
                <Image
                  src={selectedAgent.logo}
                  alt={selectedAgent.name}
                  width={40}
                  height={40}
                  className="w-10 h-10 rounded-full object-cover border border-purple-300"
                />
              )}
              <span className="text-lg font-bold text-purple-900">
                {selectedAgent ? selectedAgent.name : "Chat"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {selectedAgent && (
                <>
                  <button
                    onClick={handleOpenHistory}
                    className="text-sm text-purple-600 hover:underline mr-2"
                  >
                    See chat history
                  </button>
                  <button
                    onClick={() => {
                      setSelectedAgentId(null);
                      setMessages([]);
                      setAllMessages([]);
                      setSourceLinks([]);
                    }}
                    className="text-sm text-purple-600 hover:underline"
                  >
                    Talk to another Elder
                  </button>
                </>
              )}
              <button
                onClick={onClose}
                className="text-purple-500 hover:bg-purple-100 p-2 rounded-full transition"
                title="Close chat"
              >
                ✕
              </button>
            </div>
          </div>
        )}
        {open && panelContent}
      </div>
      {showHistoryModal && (
        <ModalContainer
          title="Full Chat History"
          onClose={() => setShowHistoryModal(false)}
          className="max-w-2xl"
        >
          <div className="space-y-4">
            {allMessages.map((m, idx) => (
              <div
                key={idx}
                className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`px-3 py-2 rounded-xl shadow max-w-[80%] ${m.role === "user" ? "bg-purple-200" : "bg-purple-50"} text-purple-900`}
                >
                  {renderMessageContent(m)}
                </div>
              </div>
            ))}
          </div>
        </ModalContainer>
      )}
    </>
  );
}
