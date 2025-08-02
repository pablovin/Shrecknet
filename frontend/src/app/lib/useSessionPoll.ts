import useSWR from "swr";
import { getSessionPoll } from "./sessionAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useSessionPoll(tableId: number, sessionId: number) {
  const { token } = useAuth();
  const fetcher = (tId: number, sId: number, t: string) =>
    getSessionPoll(tId, sId, t);
  const { data, error, mutate, isLoading } = useSWR(
    tableId && sessionId && token
      ? ["sessionPoll", tableId, sessionId, token]
      : null,
    () => fetcher(tableId, sessionId, token),
  );
  return { poll: data, error, mutate, isLoading };
}
