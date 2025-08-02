import useSWR from "swr";
import { getSessions } from "./sessionAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useSessions(tableId: number) {
  const { token } = useAuth();
  const fetcher = (id: number, t: string) => getSessions(id, t);
  const { data, error, mutate, isLoading } = useSWR(
    tableId && token ? ["sessions", tableId, token] : null,
    () => fetcher(tableId, token)
  );
  return { sessions: data || [], error, mutate, isLoading };
}
