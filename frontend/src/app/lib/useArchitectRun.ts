import useSWR from "swr";
import { useAuth } from "../components/auth/AuthProvider";
import { ArchitectRun, getArchitectRun } from "./architectAPI";

export function useArchitectRun(runId?: string | null, refresh = false) {
  const { token } = useAuth();
  const key =
    token && runId ? ["architectRun", runId, token] : null;
  const { data, error, mutate, isLoading } = useSWR<ArchitectRun>(
    key,
    () => getArchitectRun(runId as string, token || ""),
    {
      refreshInterval: refresh ? 4000 : 0,
    },
  );
  return {
    run: data,
    error,
    mutate,
    isLoading,
  };
}
