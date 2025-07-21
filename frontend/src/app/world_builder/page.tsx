"use client";
import AuthGuard from "../components/auth/AuthGuard";
import DashboardLayout from "../components/DashboardLayout";
import DashboardWorlds from "../components/worlds/DashboardWorlds";
import { useAuth } from "../components/auth/AuthProvider";
import useRoleRedirect from "../hooks/useRoleRedirect";
import { useWorlds } from "../lib/userWorlds";

export default function WorldBuilderPage() {
  const { worlds, mutate } = useWorlds();
  const { user } = useAuth();

  // Only "world builder" and "system admin" can access
  const allowed = useRoleRedirect("world builder");
  if (!allowed) return null;

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] transition-colors duration-300 px-2 sm:px-6 py-10">
          <div className="w-full max-w-6xl mx-auto">
            <div className="mb-8 flex flex-col items-center">
              <span className="text-4xl mb-1">🛠️</span>
              <h1 className="font-serif text-2xl md:text-4xl font-bold text-[var(--primary)] text-center tracking-tight mb-1">
                World Forge
              </h1>
              <div className="text-[var(--foreground)]/80 text-center text-base md:text-lg max-w-2xl">
                Build, expand, and manage all the core parts of your RPG universes: <b>concepts</b>, <b>entities</b>, <b>adventures</b> and more.<br />
                Choose a world below to start forging!
              </div>
            </div>
            {/* World selection grid - no per-world title, just grid */}
            <DashboardWorlds
              worlds={worlds}
              mutate={mutate}
              user={user}
              showEdit={true}          // World Forge needs edit access!
              showCreateButton={true}  // Optionally, allow new world creation here
              linkURL="world_builder"
              titleText={null}         // <--- No grid title; session title is above
            />
          </div>
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}
