"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import UserGrid from "../components/user_management/User_Grid";
import UserModal from "../components/user_management/User_Modal";
import { useUsers } from "../lib/useUsers";
import { useState } from "react";

export default function UserManagementPage() {
  const { user } = useAuth();
  const { users, isLoading, error, mutate } = useUsers();
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [selectedUser, setSelectedUser] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [success, setSuccess] = useState(""); 

  const allowed = useRoleRedirect("system admin");
  if (!allowed) return null;

  // Filtering logic
  const filteredUsers = (users || []).filter(u =>
    (!roleFilter || u.role === roleFilter) &&
    (u.nickname?.toLowerCase().includes(search.toLowerCase()) ||
      u.email?.toLowerCase().includes(search.toLowerCase()))
  );

  // Modal handlers
  function handleUserClick(u) {
    setSelectedUser(u);
    setModalOpen(true);
  }
  function handleModalSave() {
    mutate(); // Refresh user list
    setModalOpen(false);
    setSuccess("User updated with success!");
    setTimeout(() => setSuccess(""), 2000);
  }
  function handleModalDelete() {
    mutate(); // Refresh user list
    setModalOpen(false);
    setSuccess("User removed with success!");
    setTimeout(() => setSuccess(""), 2000);    
  }

  return (
    <AuthGuard>      
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] transition-colors duration-300 px-2 sm:px-6 py-8">
          <div className="mx-auto max-w-5xl w-full">
            <div className="flex items-center justify-between mb-7">
              <h1
                className="text-xl sm:text-2xl font-serif font-bold text-[var(--primary)] tracking-tight"
                style={{ marginBottom: 0 }}
              >
                User Management
              </h1>
              {success && (
                <div className="bg-[var(--primary)] text-[var(--primary-foreground)] px-4 py-2 rounded-xl shadow z-[1000] text-sm animate-fade-in-out">
                  {success}
                </div>
              )}
            </div>

            {/* Filters */}
            <div className="flex flex-wrap gap-4 mb-8 items-center justify-center bg-[var(--surface-variant)]/60 rounded-xl p-3">
              <input
                className="px-4 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] placeholder-[var(--primary)]/60 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] text-base shadow transition"
                placeholder="Search name or email..."
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              <select
                className="px-3 py-2 rounded-xl border border-[var(--primary)] bg-[var(--card-bg)] text-[var(--foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] text-base shadow transition"
                value={roleFilter}
                onChange={e => setRoleFilter(e.target.value)}
              >
                <option value="">All roles</option>
                <option value="player">Player</option>
                <option value="writer">Writer</option>
                <option value="world builder">World Builder</option>
                <option value="system admin">System Admin</option>
              </select>
            </div>

            {/* User List */}
            <div className="bg-[var(--card-bg)]/90 rounded-2xl shadow-2xl border border-[var(--border)] p-4 sm:p-8">
              {isLoading ? (
                <div className="text-lg text-[var(--primary)] animate-pulse">Loading users...</div>
              ) : error ? (
                <div className="text-lg text-red-500">Error loading users.</div>
              ) : (
                <UserGrid users={filteredUsers} onUserClick={handleUserClick} />
              )}
            </div>
          </div>
          {modalOpen && selectedUser && (
            <UserModal
              user={selectedUser}
              onClose={() => setModalOpen(false)}
              onSave={handleModalSave}
              onDelete={handleModalDelete}
              setError={setSuccess}
            />
          )}
        </div>
      </DashboardLayout>      
    </AuthGuard>
  );
}
