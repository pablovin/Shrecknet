"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../components/auth/AuthProvider";
import { hasRole, UserRole } from "../lib/roles";

export default function useRoleRedirect(required: UserRole) {
  const { user } = useAuth();
  const router = useRouter();
  const allowed = hasRole(user?.role, required);

  useEffect(() => {
    if (user && !allowed) {
      router.replace("/main?error=unauthorized");
    }
  }, [user, allowed, router]);

  return allowed;
}
