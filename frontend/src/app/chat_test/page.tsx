"use client";
import { useState, useRef, FormEvent } from "react";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import { useAgents } from "../lib/useAgents";
import { ChatMessage, chatTest } from "../lib/agentAPI";
import Image from "next/image";

export default function ChatTestPage() {
  const { user, token } = useAuth();
  const { agents, isLoading } = useAgents();
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const allowed = useRoleRedirect("system admin");
  if (!allowed) return null;

  const availableAgents = agents.filter(a => a.vector_db_update_date);

  function scrollToBottom() {
    setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }

  async function handleSend(e: FormEvent) {
    e.preventDefault();
    if (!input.trim() || selectedAgentId === null) return;
    const newMsg: ChatMessage = { role: "user", content: input.trim() };
    const updated = [...messages, newMsg];
    setMessages(updated);
    setInput("");
    scrollToBottom();
    setLoading(true);
    try {
      const res = await chatTest(selectedAgentId, updated, token || "");
      const docs = res.documents || [];
      const withDocs = [...updated, ...docs.map((d: any) => ({ role: "assistant", content: d.document }))];
      setMessages(withDocs);
      scrollToBottom();
    } catch {
      setMessages(m => [...m, { role: "assistant", content: "Error." }]);
    }
    setLoading(false);
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-2 sm:px-6 py-8">
          {selectedAgentId === null ? (
            <div className="mx-auto max-w-xl flex flex-col gap-4">
              <h1 className="text-xl font-bold text-[var(--primary)] text-center mb-4">Select an Agent</h1>
              {isLoading ? (
                <div className="text-center">Loading agents...</div>
              ) : (
                availableAgents.map(a => (
                  <button
                    key={a.id}
                    onClick={() => setSelectedAgentId(a.id)}
                    className="flex items-center gap-3 px-3 py-2 rounded-xl border border-[var(--primary)] hover:bg-[var(--primary)]/10"
                  >
                    <Image src={a.logo || "/images/default/avatars/logo.png"} alt={a.name} width={32} height={32} className="w-8 h-8 rounded object-cover" />
                    <span className="font-semibold text-[var(--primary)]">{a.name}</span>
                  </button>
                ))
              )}
            </div>
          ) : (
            <div className="mx-auto max-w-xl flex flex-col h-[calc(100vh-160px)]">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-bold text-[var(--primary)]">
                  {agents.find(a => a.id === selectedAgentId)?.name}
                </h2>
                <button
                  onClick={() => { setSelectedAgentId(null); setMessages([]); }}
                  className="text-sm text-[var(--primary)] hover:underline"
                >
                  Change
                </button>
              </div>
              <div className="flex-1 overflow-y-auto space-y-4 mb-2 border border-[var(--border)] rounded-xl p-3 bg-[var(--surface)]">
                {messages.map((m, idx) => (
                  <div key={idx} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div className={`${m.role === "user" ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "bg-[var(--surface-variant)]"} rounded-xl px-3 py-2 shadow max-w-[80%] whitespace-pre-wrap`}>{m.content}</div>
                  </div>
                ))}
                <div ref={messagesEndRef}></div>
              </div>
              <form onSubmit={handleSend} className="mt-2 flex gap-2">
                <input
                  className="flex-1 rounded-xl border border-[var(--primary)] p-2 bg-[var(--surface)] text-[var(--foreground)] placeholder-[var(--primary)]/70 focus:outline-none"
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  placeholder="Type your message..."
                  disabled={loading}
                />
                <button
                  type="submit"
                  className="px-4 py-2 rounded-xl bg-[var(--primary)] text-[var(--primary-foreground)] disabled:opacity-50"
                  disabled={loading || !input.trim()}
                >
                  Send
                </button>
              </form>
            </div>
          )}
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
