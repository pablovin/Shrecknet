import useSWR from "swr";
import { getTables } from "./tableAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useTables() {
  const { token } = useAuth();
  const fetcher = (t: string) => getTables(t);
  const { data, error, mutate, isLoading } = useSWR(
    token ? ["tables", token] : null,
    () => fetcher(token)
  );
  return { tables: data || [], error, mutate, isLoading };
}
