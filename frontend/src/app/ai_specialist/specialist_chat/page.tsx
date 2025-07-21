"use client";
export const dynamic = "force-dynamic";
import DashboardLayout from "../../components/DashboardLayout";
import AuthGuard from "../../components/auth/AuthGuard";
import { useSearchParams } from "next/navigation";
import { Download, File } from "lucide-react";
import Image from "next/image";
import { useAuth } from "../../components/auth/AuthProvider";
import { useAgentById } from "../../lib/useAgentById";
import {
  chatWithSpecialist,
  getSpecialistHistory,
} from "../../lib/specialistAPI";
import type { ChatMessage } from "../../lib/specialistAPI";
import { useAgentLibraryItems } from "../../lib/useAgentLibraryItems";
import { downloadBlob } from "../../lib/importExportAPI";
import { motion } from "framer-motion";
import { Download, File } from "lucide-react";
import ModalContainer from "../../components/template/modalContainer";
import { clearSpecialistHistory } from "../../lib/specialistAPI";
import { getPage } from "../../lib/pagesAPI";
import type { SourceLink } from "../../lib/agentAPI";
import { useTranslation } from "../../hooks/useTranslation";


const QUICK_REPLIES = [
  "quick_tell_me_more",
  "quick_show_sources",
  "quick_summarize",
  "quick_next_step",
  "quick_how_did_you_know",
];

// --- Typewriter Effect ---
function Typewriter({ text, done, speed = 18 }: { text: string; done: () => void; speed?: number }) {
  const [displayed, setDisplayed] = useState("");
  useEffect(() => {
    let i = 0;
    let cancelled = false;
    function type() {
      if (cancelled) return;
      setDisplayed(text.slice(0, i + 1));
      if (i < text.length - 1) {
        i++;
        setTimeout(type, speed);
      } else {
        done?.();
      }
    }
    type();
    return () => { cancelled = true; };
  }, [text]);
  return <span dangerouslySetInnerHTML={{ __html: displayed }} />;
}

function AgentAnimatedAvatar({ logo, emote = "normal" }: { logo?: string; emote?: string }) {
  // Basic: pulse or blink animation based on emote (expand with more later!)
  let animClass = "";
  if (emote === "thinking") animClass = "animate-pulse";
  if (emote === "happy") animClass = "animate-bounce";
  if (emote === "surprised") animClass = "animate-shake";
  return (
    <motion.div
      className={`relative w-14 h-14 rounded-full border-4 border-indigo-300 shadow-lg bg-white ${animClass}`}
      initial={{ scale: 0.85 }}
      animate={{ scale: 1 }}
      transition={{ type: "spring", stiffness: 200, damping: 10 }}
    >
      <Image
        src={logo || "/images/default/avatars/logo.png"}
        alt="agent"
        width={56}
        height={56}
        className="w-14 h-14 rounded-full object-cover"
        priority
      />
      {/* Emote badge: add more SVGs/emojis for different moods! */}
      <span
        className={`absolute -bottom-1 -right-1 bg-white border rounded-full px-1 text-xl ${
          emote === "happy"
            ? "text-yellow-400"
            : emote === "thinking"
            ? "text-blue-400"
            : emote === "surprised"
            ? "text-pink-600"
            : "text-indigo-400"
        }`}
      >
        {emote === "happy" ? "😃" : emote === "thinking" ? "🤔" : emote === "surprised" ? "😲" : "🧙"}
      </span>
    </motion.div>
  );
}

export function TomeCard({ src, onDownload }) {
  return (
    <motion.div
      className="
        bg-white/70 backdrop-blur-md rounded-xl shadow-sm
        border border-fuchsia-100 hover:border-fuchsia-300
        flex flex-col items-start gap-1 px-3 py-2
        min-w-[170px] max-w-[220px] transition-all
        hover:shadow-fuchsia-100/40
        cursor-pointer group
      "
      whileHover={{
        scale: 1.035,
        boxShadow: "0 2px 12px #a78bfa22",
      }}
    >
      <div className="flex items-center gap-2 w-full">
        <File className="w-4 h-4 text-fuchsia-600" />
        <span className="font-semibold text-[15px] text-neutral-800">{src.name}</span>
        <div className="ml-auto">
          <button
            className="mt-2 text-xs text-white bg-fuchsia-600 hover:bg-fuchsia-700 px-2 py-1 rounded"
            onClick={() => onDownload(src.id, src.path?.split("/").pop() || "item")}
          >
            <Download className="w-4 h-4 inline" />
          </button>
        </div>
      </div>
    </motion.div>
  );
}

// function TomeCard({ src, onDownload }: any) {
//   // fancier type/preview/tooltip logic can be added here!
//   return (
//     <motion.div
//       className="bg-white border-2 border-fuchsia-300 rounded-xl shadow flex flex-col items-center p-2 hover:scale-105 hover:shadow-lg transition-all cursor-pointer"
//       initial={{ y: 20, opacity: 0 }}
//       animate={{ y: 0, opacity: 1 }}
//       whileHover={{ scale: 1.07, boxShadow: "0 4px 16px #a78bfa44" }}
//     >
//       <div className="mb-1 flex items-center gap-1">
//         {src.type === "file" ? (
//           <File className="w-5 h-5 text-fuchsia-600" />
//         ) : (
//           <Link2 className="w-5 h-5 text-indigo-600" />
//         )}
//         <span className="font-bold text-indigo-700">{src.name}</span>
//       </div>
//       <span className="text-xs text-indigo-400">{src.path ? src.path.split("/").pop() : src.url}</span>
//       {src.type === "file" ? (
//         <button
//           className="mt-2 text-xs text-white bg-fuchsia-600 hover:bg-fuchsia-700 px-2 py-1 rounded"
//           onClick={() => onDownload(src.id!, src.path?.split("/").pop() || "source")}
//         >
//           <Download className="w-4 h-4 inline" /> Download
//         </button>
//       ) : src.url ? (
//         <a
//           href={src.url}
//           target="_blank"
//           rel="noopener noreferrer"
//           className="mt-2 text-xs text-indigo-700 underline flex items-center gap-1"
//         >
//           <BookOpen className="w-4 h-4" />
//           Visit
//         </a>
//       ) : null}
//     </motion.div>
//   );
// }

function SpecialistChatPageContent() {
  const { user, token } = useAuth();
  const { t } = useTranslation();
  const searchParams = useSearchParams();
  const agentId = parseInt(searchParams.get("agent") || "0");
  const { agent } = useAgentById(agentId);
  const { items: sources } = useAgentLibraryItems(agentId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [allMessages, setAllMessages] = useState<ChatMessage[]>([]);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [showDownloadModal, setShowDownloadModal] = useState(false);
  const [toastMsg, setToastMsg] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);

  interface SourceInfo { title: string; url?: string; logo?: string }
  const [sourceInfos, setSourceInfos] = useState<SourceInfo[]>([]);

  // For RPG "emote" effect
  const [agentEmote, setAgentEmote] = useState<"normal" | "thinking" | "happy" | "surprised">("normal");

  async function updateSourceInfos(sources: SourceLink[] = []) {
    if (!sources.length) {
      setSourceInfos([]);
      return;
    }
    const infos: SourceInfo[] = await Promise.all(
      sources.map(async (s) => {
        const src: any = s;
        let title = src.title || src.name || "";
        let url: string | undefined = src.url;
        let logo: string | undefined;
        if (url) {
          const match = url.match(/page\/(\d+)/);
          if (match) {
            try {
              const page = await getPage(Number(match[1]), token || "");
              title = page.name;
              logo = page.logo;
            } catch {}
          }
        }
        return { title, url, logo };
      })
    );
    setSourceInfos(infos);
  }

  useEffect(() => {
    if (!agentId) return;
    getSpecialistHistory(agentId, token || "", 10)
      .then((msgs) => {
        setAllMessages(msgs as ChatMessage[]);
        setMessages((msgs as ChatMessage[]).slice(-10));
        const last = [...msgs as ChatMessage[]].reverse().find(m => m.role === "assistant" && m.sources);
        if (last && last.sources) updateSourceInfos(last.sources);
      })
      .catch(() => {
        setAllMessages([]);
        setMessages([]);
        setSourceInfos([]);
      });
  }, [agentId, token]);

  function scrollToBottom() {
    setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }

  async function handleSend(e: FormEvent, quick?: string) {
    e?.preventDefault();
    if ((!input.trim() && !quick) || !agentId) return;
    const userMsg = quick || input.trim();
    const newMsg: ChatMessage = { role: "user", content: userMsg };
    const updatedAll = [...allMessages, newMsg];
    setAllMessages(updatedAll);
    const display = [...messages, newMsg].slice(-10);
    setMessages(display);
    setInput("");
    scrollToBottom();
    setLoading(true);
    setAgentEmote("thinking");
    setMessages((m) => [...m, { role: "assistant", content: "" }]);
    try {
      const { answer, sources } = await chatWithSpecialist(agentId, updatedAll, token || "");
      setAllMessages((m) => [...m, { role: "assistant", content: answer, sources }]);
      setMessages((m) => {
        const arr = [...m];
        arr[arr.length - 1] = { role: "assistant", content: answer, sources };
        return arr.slice(-10);
      });
      updateSourceInfos(sources);
      setAgentEmote("happy");
      scrollToBottom();
    } catch {
      setAllMessages((m) => [...m, { role: "assistant", content: t('error_generic') }]);
      setMessages((m) => [
        ...m.slice(0, -1),
        { role: "assistant", content: t('error_generic') },
      ].slice(-10));
      updateSourceInfos([]);
      setAgentEmote("surprised");
    }
    setTimeout(() => setAgentEmote("normal"), 2200);
    setLoading(false);
  }

  async function handleDownload(itemId: number, filename: string) {
    setShowDownloadModal(true);
    try {
      const { downloadLibraryItem } = await import("../../lib/libraryAPI");
      const blob = await downloadLibraryItem(itemId, token || "");
      downloadBlob(blob, filename);
    } catch (err) {
      // You can trigger an emote or show a toast here!
      setAgentEmote("surprised");
      console.error("Failed to download source", err);
    } finally {
      setShowDownloadModal(false);
    }
  }

  async function handleClearChat() {
    if (!agentId) return;
    if (!confirm(t('confirm_delete_history'))) return;
    try {
      await clearSpecialistHistory(agentId, token || "");
      setMessages([]);
      setAllMessages([]);
      setSourceInfos([]);
      setToastMsg(t('chat_history_cleared'));
    } catch (err) {
      setToastMsg(t('chat_history_clear_failed'));
      console.error(err);
    } finally {
      setTimeout(() => setToastMsg(""), 3000);
    }
  }

  async function handleOpenHistory() {
    if (!agentId) return;
    try {
      const full = await getSpecialistHistory(agentId, token || "", 1000);
      setAllMessages(full as ChatMessage[]);
      setShowHistoryModal(true);
    } catch {
      setShowHistoryModal(true);
    }
  }

  function renderMessageContent(msg: ChatMessage, idx: number, isAssistant: boolean) {
    // If last assistant message and loading, use typewriter
    if (isAssistant && loading && idx === messages.length - 1) {
      return (
        <Typewriter
          text={msg.content}
          done={() => {
            /* Optionally auto-scroll etc */
          }}
        />
      );
    }
    return (
      <>
        <span dangerouslySetInnerHTML={{ __html: msg.content }} />
      </>
    );
  }

  if (!agentId) {
    return (
      <div className="p-10 text-lg text-center font-bold text-fuchsia-800">
        {t('no_specialist_selected')}
      </div>
    );
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        {toastMsg && (
          <div className="fixed top-8 left-1/2 transform -translate-x-1/2 bg-fuchsia-600 text-white px-4 py-2 rounded-xl shadow-lg z-[1000] text-sm animate-fade-in-out">
            {toastMsg}
          </div>
        )}
        <div
          className={`min-h-screen w-full flex flex-col items-center px-1 sm:px-6 py-6 transition-all duration-700`}
        >
          {/* Topbar: Agent Presence */}
          <div className="flex items-center gap-3 mb-5 drop-shadow-xl">
            <AgentAnimatedAvatar logo={agent?.logo} emote={agentEmote} />
            <div>
              <h2 className="text-2xl font-bold text-fuchsia-700 tracking-wide flex gap-1 items-center">
                {agent?.name || "Specialist"}
                <span className="text-base ml-2 px-2 py-1 rounded bg-fuchsia-100 border border-fuchsia-300 text-fuchsia-700 font-mono">
                  NPC
                </span>
              </h2>
              <div className="text-sm text-fuchsia-500 italic">
                {agentEmote === "thinking"
                  ? t('agent_pondering')
                  : agentEmote === "happy"
                  ? t('agent_excited')
                  : agentEmote === "surprised"
                  ? t('agent_startled')
                  : t('agent_ready')}
              </div>
            </div>

          </div>

          <div className="w-full sm:w-[80%] mx-auto flex flex-col h-[calc(100vh-160px)]">
            <div className="flex items-center gap-2 mb-2">
              <button
                className="ml-auto text-xs text-fuchsia-600 underline"
                onClick={handleOpenHistory}
              >
                {t('see_chat_history')}
              </button>
              <button
                className="text-xs text-red-600 underline"
                onClick={handleClearChat}
              >
                {t('clear_chat')}
              </button>
            </div>

            <div className="mb-3 overflow-x-auto flex gap-3 pb-2">
              {sources.map((src) => (
                <TomeCard key={src.id} src={src} onDownload={handleDownload} />
              ))}
            </div>

            <div className="flex-1 overflow-y-auto space-y-5 mb-2 border border-fuchsia-200 rounded-2xl bg-white/90 shadow-lg p-4">
              {messages.map((m, idx) => (
                <div
                  key={idx}
                  className={`flex w-full ${
                    m.role === "user" ? "justify-end" : "justify-start"
                  }`}
                >
                  {m.role === "assistant" && (
                    <AgentAnimatedAvatar logo={agent?.logo} emote={agentEmote} />
                  )}
                    <motion.div
                      className={`${
                        m.role === "user"
                          ? "bg-fuchsia-600 text-white rounded-br-lg rounded-tl-2xl rounded-tr-2xl"
                          : "bg-fuchsia-50 text-fuchsia-900 rounded-bl-lg rounded-tr-2xl rounded-tl-2xl"
                      } px-4 py-2 shadow-md max-w-[78%] ml-2 mr-2 font-medium relative`}
                      initial={{ scale: 0.93, y: 14, opacity: 0.7 }}
                      animate={{ scale: 1, y: 0, opacity: 1 }}
                      transition={{ type: "spring", stiffness: 260, damping: 18 }}
                    >
                      {m.role === "assistant" && (
                        <div className="text-xs font-semibold text-fuchsia-700 mb-1">
                          {agent?.name}
                        </div>
                      )}
                      {renderMessageContent(m, idx, m.role === "assistant")}
                    </motion.div>
                    {m.role === "user" && user?.image_url && (
                      <motion.div
                        initial={{ scale: 0.85 }}
                        animate={{ scale: 1 }}
                        className="w-10 h-10 rounded-full border border-fuchsia-300 shadow-lg ml-2"
                      >
                        <Image
                          src={user.image_url}
                          alt="me"
                          width={40}
                          height={40}
                          className="w-10 h-10 rounded-full object-cover"
                        />
                      </motion.div>
                    )}
                  </div>
                ))}
                <div ref={messagesEndRef} />
              </div>
            
            

            <form onSubmit={handleSend} className="mt-3 flex gap-2">
              <textarea
                className="flex-1 rounded-xl border border-fuchsia-300 p-2 bg-white text-fuchsia-900 resize-y shadow-sm"
                rows={2}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder={
                    loading
                      ? t('specialist_thinking')
                      : t('specialist_input_placeholder')
                  }
                  disabled={loading}
                />
                <button
                  type="submit"
                  className="px-5 py-2 rounded-xl bg-fuchsia-600 text-white font-bold shadow-md hover:bg-fuchsia-700 disabled:opacity-60"
                  disabled={loading || !input.trim()}
                >
                  {t('send')}
                </button>
              </form>
          </div>
        </div>
        {showHistoryModal && (
          <ModalContainer title={t('full_chat_history')} onClose={() => setShowHistoryModal(false)} className="max-w-3xl">
            <div className="space-y-4">
              {allMessages.map((m, idx) => (
                <div
                  key={idx}
                  className={`flex w-full ${m.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`px-3 py-2 rounded-xl shadow max-w-[80%] ${m.role === "user" ? "bg-fuchsia-200" : "bg-fuchsia-50"} text-fuchsia-900`}
                  >
                    {renderMessageContent(m, idx, m.role === "assistant")}
                  </div>
                </div>
              ))}
            </div>
          </ModalContainer>
        )}
        {showDownloadModal && (
          <ModalContainer title={t('preparing_download')} onClose={() => setShowDownloadModal(false)}>
            <div className="flex flex-col items-center gap-4 text-fuchsia-700">
              <AgentAnimatedAvatar logo={agent?.logo} emote="thinking" />
              <p className="text-center text-base font-semibold">
                {t('preparing_download_text')}
              </p>
            </div>
          </ModalContainer>
        )}
      </DashboardLayout>
    </AuthGuard>
  );
}

export default function SpecialistChatPage() {
  return (
    <Suspense>
      <SpecialistChatPageContent />
    </Suspense>
  );
}
