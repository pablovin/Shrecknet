import useSWR from "swr";
import { getSessions } from "./sessionAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useSessions(tableId: number, joined = false) {
  const { token } = useAuth();
  const fetcher = (id: number, t: string, j: boolean) => getSessions(id, t, j);
  const { data, error, mutate, isLoading } = useSWR(
    tableId && token ? ["sessions", tableId, token, joined] : null,
    () => fetcher(tableId, token, joined),
  );
  return { sessions: data || [], error, mutate, isLoading };
}
