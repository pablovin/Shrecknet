import useSWR from "swr";
import { getLibraryItems } from "./libraryAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useLibraryItems() {
  const { token } = useAuth();

  const fetcher = (t: string) => getLibraryItems(t);

  const { data, error, mutate, isLoading } = useSWR(
    token ? ["library", token] : null,
    () => fetcher(token)
  );

  return {
    items: data || [],
    error,
    mutate,
    isLoading,
  };
}
