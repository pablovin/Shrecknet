"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import { useState } from "react";
import { useTranslation } from "@/app/hooks/useTranslation";
import { hasRole } from "../lib/roles";
import useRoleRedirect from "../hooks/useRoleRedirect";
import ImportWorldModal from "../components/importexport/ImportWorldModal";
import ExportWorldModal from "../components/importexport/ExportWorldModal";
import ImportBackupModal from "../components/importexport/ImportBackupModal";
import { createBackup } from "../lib/backupAPI";
import { downloadBlob } from "../lib/importExportAPI";
import {
  Upload,
  Download,
  FileDown,
  FileUp,
  BookOpenText,
  Bot,
  History,
  Users2,
  Book,
  Layers,
} from "lucide-react";
import SimpleBarChart from "../components/charts/SimpleBarChart";
import { useLibraryItems } from "../lib/useLibraryItems";
import { useWorlds } from "../lib/userWorlds";
import { useAgents } from "../lib/useAgents";
import { useJobs } from "../lib/useJobs";
import { useUsers } from "../lib/useUsers";
import Link from "next/link";

export default function AdminDashboardPage() {
  const { user, token } = useAuth();
  const { t } = useTranslation();
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [backupModalOpen, setBackupModalOpen] = useState(false);
  const [loadingBackup, setLoadingBackup] = useState(false);
  const [success, setSuccess] = useState("");

  const { items: libraryItems } = useLibraryItems();
  const { worlds } = useWorlds();
  const { agents } = useAgents();
  const { jobs } = useJobs();
  const { users: userList } = useUsers();

  async function handleCreateBackup() {
    if (!token) return;
    setLoadingBackup(true);
    try {
      const blob = await createBackup(token);
      downloadBlob(blob, `backup.zip`);
      setSuccess(t("export_started"));
      setTimeout(() => setSuccess(""), 2000);
    } catch (err) {
      setSuccess(t("backup_failed"));
    } finally {
      setLoadingBackup(false);
    }
  }

    const allowed = useRoleRedirect("system admin");
    if (!allowed) return null;

  // --- Data aggregation for dashboard charts ---
  const rulebookCounts = libraryItems.reduce<Record<string, number>>((acc, it) => {
    acc[it.system] = (acc[it.system] || 0) + 1;
    return acc;
  }, {});
  const libraryData = Object.entries(rulebookCounts)
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value);

  const worldNames = worlds.reduce<Record<number, string>>((acc, w) => {
    acc[w.id] = w.name;
    return acc;
  }, {});
  const agentCounts: Record<string, Record<string, number>> = {};
  agents.forEach(a => {
    const w = worldNames[a.world_id] || `World ${a.world_id}`;
    if (!agentCounts[w]) agentCounts[w] = {};
    agentCounts[w][a.task] = (agentCounts[w][a.task] || 0) + 1;
  });
  const agentWorlds = Object.entries(agentCounts).map(([world, counts]) => ({ world, counts }));

  const jobStats = { done: 0, failed: 0, processing: 0 };
  jobs.forEach(j => {
    if (j.status === "done") jobStats.done += 1;
    else if (j.status === "error") jobStats.failed += 1;
    else jobStats.processing += 1;
  });
  const jobData = [
    { label: "Done", value: jobStats.done },
    { label: "Processing", value: jobStats.processing },
    { label: "Failed", value: jobStats.failed },
  ];

  const roleCounts = userList.reduce<Record<string, number>>((acc, u) => {
    acc[u.role] = (acc[u.role] || 0) + 1;
    return acc;
  }, {});
  const userData = Object.entries(roleCounts).map(([label, value]) => ({ label, value }));

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] px-4 sm:px-6 py-10 transition-colors duration-300">
          <div className="mx-auto max-w-7xl w-full space-y-12">
            <h1 className="text-3xl font-serif font-bold text-[var(--primary)] tracking-tight">
              ✨ System Control Deck
            </h1>

            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-6">
              <DashboardCard
                title="Import & Export Worlds"
                description="Transfer your worlds between servers or creators."
                icon={<Upload className="w-6 h-6" />}
                color="from-purple-600 to-fuchsia-500"
                actions={
                  <>
                    <button className="btn-primary" onClick={() => setImportModalOpen(true)}>
                      <Upload className="w-4 h-4" /> Import
                    </button>
                    <button className="btn-outline" onClick={() => setExportModalOpen(true)}>
                      <Download className="w-4 h-4" /> Export
                    </button>
                  </>
                }
              />

              <DashboardCard
                title="Backup & Restore"
                description="Safeguard your progress. Recover from magical mishaps."
                icon={<FileDown className="w-6 h-6" />}
                color="from-cyan-600 to-blue-500"
                actions={
                  <>
                    <button className="btn-outline" disabled={loadingBackup} onClick={handleCreateBackup}>
                      {loadingBackup ? "Processing..." : "Create Backup"}
                    </button>
                    <button className="btn-primary" onClick={() => setBackupModalOpen(true)}>
                      <FileUp className="w-4 h-4" /> Restore
                    </button>
                  </>
                }
              />

              <DashboardCard
                title="Library Vault"
                description="Manage your tomes, rulebooks, and arcane references."
                icon={<BookOpenText className="w-6 h-6" />}
                color="from-orange-500 to-amber-400"
                extra={<SimpleBarChart data={libraryData} />}
                actions={
                  <Link className="btn-primary" href="/library_admin">
                    Enter Library
                  </Link>
                }
              />

              <DashboardCard
                title="World Embeddings"
                description="Manage vector stores for your worlds."
                icon={<Layers className="w-6 h-6" />}
                color="from-violet-600 to-purple-500"
                actions={
                  <Link className="btn-primary" href="/world_embeddings">
                    Manage Embeddings
                  </Link>
                }
              />

              <DashboardCard
                title="Agent Interface"
                description="Control your world’s autonomous NPC advisors and guardians."
                icon={<Bot className="w-6 h-6" />}
                color="from-emerald-600 to-lime-500"
                extra={<AgentSummary data={agentWorlds} />}
                actions={
                  <Link className="btn-primary" href="/agents_settings">
                    Configure Agents
                  </Link>
                }
              />

              <DashboardCard
                title="Chrono Queue"
                description="Review and manage long-running background operations."
                icon={<History className="w-6 h-6" />}
                color="from-sky-600 to-indigo-500"
                extra={<SimpleBarChart data={jobData} />}
                actions={
                  <Link className="btn-primary" href="/background_jobs">
                    View Jobs
                  </Link>
                }
              />

              <DashboardCard
                title="User Sanctum"
                description="Manage users, roles and permissions across your dominion."
                icon={<Users2 className="w-6 h-6" />}
                color="from-pink-500 to-rose-500"
                extra={<SimpleBarChart data={userData} />}
                actions={
                  <Link className="btn-primary" href="/user_management">
                    Manage Users
                  </Link>
                }
              />
            </div>

            {/* Modals */}
            <ImportWorldModal open={importModalOpen} onClose={() => setImportModalOpen(false)} onImported={() => setSuccess("World imported!")} />
            <ExportWorldModal open={exportModalOpen} onClose={() => setExportModalOpen(false)} />
            <ImportBackupModal open={backupModalOpen} onClose={() => setBackupModalOpen(false)} onImported={() => setSuccess("Backup imported!")} />
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}

function DashboardCard({ title, description, icon, color, actions, extra }) {
  return (
    <div className="group relative overflow-hidden rounded-3xl border border-[var(--border)] bg-[var(--surface)] p-6 shadow-md transition hover:shadow-xl hover:border-[var(--primary)]/60">
      {/* Accent glow */}
      <div
        className="absolute -top-5 -right-5 w-28 h-28 rounded-full bg-gradient-to-br opacity-30 blur-2xl pointer-events-none z-0"
        style={{ backgroundImage: `linear-gradient(${color})` }}
      ></div>

      {/* Header Row */}
      <div className="relative z-10 flex items-center gap-4 mb-4">
        <div className={`w-11 h-11 rounded-2xl flex items-center justify-center bg-gradient-to-br ${color} text-white shadow-lg`}>
          {icon}
        </div>
        <div>
          <h2 className="text-lg font-serif font-bold text-[var(--primary)] leading-tight">{title}</h2>
          <p className="text-xs text-[var(--foreground)]/60">{description}</p>
        </div>
      </div>

      {/* Extra Content */}
      {extra && (
        <div className="relative z-10 mb-5 bg-[var(--surface-variant)]/60 rounded-xl p-3 border border-[var(--border)] shadow-inner">
          {extra}
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-3 z-10 relative">{actions}</div>
    </div>
  );
}

function AgentSummary({ data }: { data: { world: string; counts: Record<string, number> }[] }) {
  const icons: Record<string, JSX.Element> = {
    conversational: <Bot className="w-3 h-3" />,
    "page writer": <BookOpenText className="w-3 h-3" />,
    "story novelist": <Book className="w-3 h-3" />,
    specialist: <Users2 className="w-3 h-3" />,
  };

  const colors: Record<string, string> = {
    conversational: "var(--chart-1)",
    "page writer": "var(--chart-2)",
    "story novelist": "var(--chart-3)",
    specialist: "var(--chart-4)",
  };

  return (
    <div className="space-y-2 text-sm bg-[var(--surface-variant)]/50 p-3 rounded-xl border border-[var(--border)]">
      {data.map(({ world, counts }) => (
        <div key={world} className="flex flex-col">
          <span className="font-semibold text-[var(--primary)] font-serif">{world}</span>
          <div className="flex gap-2 flex-wrap ml-1 mt-2">
            {Object.entries(counts).map(([type, count]) => {
              const icon = icons[type];
              const color = colors[type] ?? "var(--foreground)";
              return (
                <span
                  key={type}
                  className="flex items-center gap-1 px-2 py-1 text-xs rounded-full bg-[var(--card-bg)] border border-[var(--border)] shadow-sm"
                  style={{ color }}
                >
                  {icon ?? <span className="w-3 h-3 rounded-full bg-[var(--foreground)]/40 inline-block" />} {count}
                </span>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}