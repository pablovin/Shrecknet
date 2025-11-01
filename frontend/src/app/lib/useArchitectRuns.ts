import useSWR from "swr";
import { useAuth } from "../components/auth/AuthProvider";
import { listArchitectRuns, ArchitectRunSummary } from "./architectAPI";

export function useArchitectRuns(agentId?: number | null) {
  const { token } = useAuth();
  const fetcher = () =>
    listArchitectRuns(agentId as number, token || "", { limit: 50, offset: 0 });
  const key =
    token && agentId ? ["architectRuns", agentId, token] : null;
  const { data, error, mutate, isLoading } = useSWR<ArchitectRunSummary[]>(key, fetcher, {
    refreshInterval: 4000,
  });
  return {
    runs: data || [],
    error,
    mutate,
    isLoading,
  };
}
