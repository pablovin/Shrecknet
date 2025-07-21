"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { hasRole } from "../lib/roles";
import { useAuth } from "../components/auth/AuthProvider";
import { useState } from "react";
import { useTranslation } from "@/app/hooks/useTranslation";
import ImportWorldModal from "../components/importexport/ImportWorldModal";
import { Download, Upload, Users2, Bot, PenLine, FileDown, FileUp, BookOpenText } from "lucide-react";
import { History } from "lucide-react";
import Link from "next/link";
import ExportWorldModal from "../components/importexport/ExportWorldModal";
import ImportBackupModal from "../components/importexport/ImportBackupModal";
import { createBackup } from "../lib/backupAPI";
import { downloadBlob } from "../lib/importExportAPI";


export default function UserManagementPage() {
  const { user, token } = useAuth();
  const { t } = useTranslation();
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [success, setSuccess] = useState("");
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [backupModalOpen, setBackupModalOpen] = useState(false);
  const [loadingBackup, setLoadingBackup] = useState(false);

  async function handleCreateBackup() {
    if (!token) return;
    setLoadingBackup(true);
    try {
      const blob = await createBackup(token);
      downloadBlob(blob, `backup.zip`);
      setSuccess(t('export_started'));
      setTimeout(() => setSuccess(""), 2000);
    } catch (err) {
      setSuccess(t('backup_failed'));
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
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] transition-colors duration-300 px-2 sm:px-6 py-8">
          <div className="mx-auto max-w-3xl w-full flex flex-col gap-8">
            {/* Title & feedback */}
            <div className="flex items-center justify-between mb-3">
              <h1 className="text-2xl font-serif font-bold text-[var(--primary)] tracking-tight">
                System Settings
              </h1>
              <div className="flex flex-col gap-1 items-end">
                {success && (
                  <div className="bg-[var(--primary)] text-[var(--primary-foreground)] px-4 py-2 rounded-xl shadow z-[1000] text-sm animate-fade-in-out">
                    {success}
                  </div>
                )}
              </div>
            </div>

            {/* Import/Export Area */}
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-sm p-6 w-full flex flex-col sm:flex-row gap-6 items-center">
              <div className="flex flex-1 flex-col gap-2">
                <div className="text-[var(--primary)] font-bold text-lg mb-1">
                  Import / Export World
                </div>
                <div className="text-[var(--foreground)]/80 text-sm mb-3">
                  Import a new world (from JSON), or export the current world for backup or transfer.
                </div>
                <div className="flex gap-4">
                  <button
                    type="button"
                    className="flex items-center gap-2 px-5 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)]
                      shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition border border-[var(--primary)]"
                    onClick={() => setImportModalOpen(true)}
                  >
                    <Upload className="w-5 h-5" />
                    Import World
                  </button>
                  <button
                  type="button"
                  className="flex items-center gap-2 px-5 py-2 rounded-xl font-bold border border-[var(--primary)] shadow transition
                    bg-transparent text-[var(--primary)] hover:bg-[var(--primary)]/10 hover:text-[var(--primary-foreground)]"
                  onClick={() => setExportModalOpen(true)}
                >
                  <Download className="w-5 h-5" />
                  Export World
                </button>
                </div>
              </div>
              <ImportWorldModal
                open={importModalOpen}
                onClose={() => setImportModalOpen(false)}
                onImported={() => {
                  setSuccess("World imported successfully!");
                  setImportModalOpen(false);
                  setTimeout(() => setSuccess(""), 2000);
                }}
              />
            <ExportWorldModal open={exportModalOpen} onClose={() => setExportModalOpen(false)} />
            </div>

            {/* Backup Area */}
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-sm p-6 w-full flex flex-col sm:flex-row gap-6 items-center">
              <div className="flex flex-1 flex-col gap-2">
                <div className="text-[var(--primary)] font-bold text-lg mb-1">Backup / Restore</div>
                <div className="text-[var(--foreground)]/80 text-sm mb-3">
                  Create a full backup of all data and uploads, or import a backup to completely replace current data.
                </div>
                <div className="flex gap-4">
                  <button
                    type="button"
                    className={`flex items-center gap-2 px-5 py-2 rounded-xl font-bold border border-[var(--primary)] shadow transition ${loadingBackup ? 'bg-[var(--primary)]/10 text-[var(--primary)]/60' : 'bg-transparent text-[var(--primary)] hover:bg-[var(--primary)]/10 hover:text-[var(--primary-foreground)]'}`}
                    onClick={handleCreateBackup}
                    disabled={loadingBackup}
                  >
                    <FileDown className="w-5 h-5" />
                    {loadingBackup ? t('processing') : t('create_backup')}
                  </button>
                  <button
                    type="button"
                    className="flex items-center gap-2 px-5 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] transition border border-[var(--primary)]"
                    onClick={() => setBackupModalOpen(true)}
                  >
                    <FileUp className="w-5 h-5" />
                    {t('import_backup')}
                  </button>
                </div>
              </div>
              <ImportBackupModal
                open={backupModalOpen}
                onClose={() => setBackupModalOpen(false)}
                onImported={() => {
                  setSuccess('Backup imported successfully!');
                  setTimeout(() => setSuccess(''), 2000);
                }}
              />
            </div>

            {/* Library Admin Area */}
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-sm p-6 w-full flex flex-col gap-2">
              <div className="flex items-center gap-2 mb-2">
                <BookOpenText className="w-6 h-6 text-[var(--primary)]" />
                <div className="text-[var(--primary)] font-bold text-lg">Library Admin</div>
              </div>
              <div className="text-[var(--foreground)]/80 text-sm mb-3">
                Manage your collection of rulebooks and supplements.
              </div>
              <Link
                href="/library_admin"
                className="inline-flex items-center gap-2 px-5 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] border border-[var(--primary)] transition w-fit"
              >
                <BookOpenText className="w-5 h-5" />
                Go to Library Admin
              </Link>
            </div>

            {/* Agents Settings Area */}
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-sm p-6 w-full flex flex-col gap-2">
              <div className="flex items-center gap-2 mb-2">
                <Bot className="w-6 h-6 text-[var(--primary)]" />
                <div className="text-[var(--primary)] font-bold text-lg">Agent Settings</div>
              </div>
              <div className="text-[var(--foreground)]/80 text-sm mb-3">
                Manage NPC agents and their configuration.
              </div>
              <Link
                href="/agents_settings"
                className="inline-flex items-center gap-2 px-5 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] border border-[var(--primary)] transition w-fit"
              >
                <Bot className="w-5 h-5" />
                Go to Agent Settings
              </Link>
            </div>

            {/* Background Jobs Area */}
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-sm p-6 w-full flex flex-col gap-2">
              <div className="flex items-center gap-2 mb-2">
                <History className="w-6 h-6 text-[var(--primary)]" />
                <div className="text-[var(--primary)] font-bold text-lg">Background Jobs</div>
              </div>
              <div className="text-[var(--foreground)]/80 text-sm mb-3">
                View and manage background job history.
              </div>
              <Link
                href="/background_jobs"
                className="inline-flex items-center gap-2 px-5 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow hover:bg-[var(--accent)] hover:text-[var(--background)] border border-[var(--primary)] transition w-fit"
              >
                <History className="w-5 h-5" />
                Open Background Jobs
              </Link>
            </div>

            
            {/* User Management Area */}
            <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-sm p-6 w-full flex flex-col gap-2">
              <div className="flex items-center gap-2 mb-2">
                <Users2 className="w-6 h-6 text-[var(--primary)]" />
                <div className="text-[var(--primary)] font-bold text-lg">
                  User Management
                </div>
              </div>
              <div className="text-[var(--foreground)]/80 text-sm mb-3">
                Manage all users, roles and permissions.
              </div>
              <Link
                href="/user_management"
                className="inline-flex items-center gap-2 px-5 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)]
                  shadow hover:bg-[var(--accent)] hover:text-[var(--background)] border border-[var(--primary)] transition w-fit"
              >
                <Users2 className="w-5 h-5" />
                Go to User Management
              </Link>
            </div>
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
