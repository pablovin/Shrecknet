"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import { useAgents } from "../lib/useAgents";
import { useWorlds } from "../lib/userWorlds";
import Image from "next/image";
import { BookOpen, PenLine } from "lucide-react";
import { useRouter } from "next/navigation";

export default function AINovelistPage() {
  const { user } = useAuth();
  const { agents, isLoading } = useAgents();
  const { worlds } = useWorlds();
  const router = useRouter();

  const allowed = useRoleRedirect("writer");
  if (!allowed) return null;

  const novelists = agents.filter(a => a.task === "story novelist");
  const worldName = (id: number) => worlds.find(w => w.id === id)?.name || "";

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full text-indigo-900 px-2 sm:px-6 py-12">
          <div className="mx-auto max-w-3xl flex flex-col gap-8 items-center">
            <div className="w-full flex flex-col items-center gap-4 bg-gradient-to-br from-indigo-100/70 via-purple-100/80 to-white/80 rounded-2xl shadow-xl p-8 border border-indigo-200">
              <BookOpen className="w-12 h-12 text-indigo-400 mb-2" />
              <h1 className="text-3xl font-bold text-indigo-700 text-center font-serif mb-1">Choose your Novelist</h1>
              <p className="text-center text-lg text-indigo-900/80 mb-2">
                Let an AI Novelist weave epic stories from your world's lore.
              </p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-8 mt-6 w-full">
              {isLoading ? (
                <div className="col-span-2 text-center text-lg">Summoning Novelists...</div>
              ) : (
                novelists.map(a => (
                  <button
                    key={a.id}
                    onClick={() => router.push(`/ai_novelist/create_novel?agent=${a.id}`)}
                    className="flex flex-col items-center gap-2 p-6 rounded-2xl shadow-lg border border-indigo-200 bg-white hover:scale-105 hover:shadow-2xl transition-all"
                  >
                    <Image src={a.logo || "/images/default/avatars/logo.png"} alt={a.name} width={100} height={100} className="rounded-full object-cover border-2 border-fuchsia-300 shadow mb-2" />
                    <span className="text-xl font-bold text-indigo-800">{a.name}</span>
                    <span className="text-sm text-fuchsia-700">{worldName(a.world_id)}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
