import useSWR from "swr";
import { getUserNote } from "./userNotesAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useUserNote(noteId?: number) {
  const { token } = useAuth();
  const fetcher = () => getUserNote(noteId!, token || "");
  const { data, error, mutate, isLoading } = useSWR(noteId && token ? ["user_note", noteId, token] : null, fetcher);
  return { note: data || null, error, mutate, isLoading };
}
