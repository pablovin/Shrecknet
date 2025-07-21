import useSWR from "swr";
import { listAgentItems, AgentLibraryItem } from "./specialistAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useAgentLibraryItems(agentId: number) {
  const { token } = useAuth();
  const fetcher = () => listAgentItems(agentId, token || "");
  const { data, error, mutate } = useSWR(
    token && agentId ? ["agent-items", agentId, token] : null,
    fetcher
  );
  return { items: (data as AgentLibraryItem[]) || [], error, mutate };
}
