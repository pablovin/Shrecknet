import useSWR from "swr";
import { getUserNotes } from "./userNotesAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useUserNotes() {
  const { token } = useAuth();
  const fetcher = () => getUserNotes(token || "");
  const { data, error, mutate, isLoading } = useSWR(token ? ["user_notes", token] : null, fetcher);
  return { notes: data || [], error, mutate, isLoading };
}
