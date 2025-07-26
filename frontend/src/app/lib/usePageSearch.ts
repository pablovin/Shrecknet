import useSWR from "swr";
import { searchPages } from "./pagesAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function usePageSearch(term: string) {
  const { token } = useAuth();
  const fetcher = () => searchPages(term, token);
  const { data, error, isLoading } = useSWR(
    term.length >= 2 && token ? ["pageSearch", term, token] : null,
    fetcher
  );
  return { pages: data || [], error, isLoading };
}
