"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import { useState } from "react";
import { useTranslation } from "@/app/hooks/useTranslation";
import { hasRole } from "../lib/roles";
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
} from "lucide-react";
import Link from "next/link";

export default function AdminDashboardPage() {
  const { user, token } = useAuth();
  const { t } = useTranslation();
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [backupModalOpen, setBackupModalOpen] = useState(false);
  const [loadingBackup, setLoadingBackup] = useState(false);
  const [success, setSuccess] = useState("");

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

  if (!hasRole(user?.role, "system admin")) {
    return (
      <DashboardLayout>
        <div className="p-10 text-2xl text-red-600 font-bold">Not authorized</div>
      </DashboardLayout>
    );
  }

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
                actions={
                  <Link className="btn-primary" href="/library_admin">
                    Enter Library
                  </Link>
                }
              />

              <DashboardCard
                title="Agent Interface"
                description="Control your world’s autonomous NPC advisors and guardians."
                icon={<Bot className="w-6 h-6" />}
                color="from-emerald-600 to-lime-500"
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

function DashboardCard({ title, description, icon, color, actions }) {
  return (
    <div className="group relative overflow-hidden rounded-3xl border border-[var(--border)] bg-[var(--surface)] p-6 shadow-md transition hover:shadow-xl">
      {/* Accent circle and icon */}
      <div className="absolute -top-5 -right-5 w-24 h-24 rounded-full bg-gradient-to-br opacity-30 blur-2xl pointer-events-none z-0" style={{ backgroundImage: `linear-gradient(${color})` }}></div>
      <div className="relative z-10 flex items-center gap-3 mb-3">
        <div className={`w-10 h-10 rounded-xl flex items-center justify-center bg-gradient-to-br ${color} text-white shadow-md`}>
          {icon}
        </div>
        <h2 className="text-lg font-bold text-[var(--primary)]">{title}</h2>
      </div>
      <p className="text-sm text-[var(--foreground)]/80 mb-4">{description}</p>
      <div className="flex flex-wrap gap-3 z-10 relative">{actions}</div>
    </div>
  );
}