import useSWR from "swr";
import { listWorldEmbeddingJobs } from "./worldEmbeddingAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useWorldEmbeddingJobs() {
  const { token } = useAuth();
  const fetcher = () => listWorldEmbeddingJobs(token || "");
  const { data, error, mutate } = useSWR(
    token ? ["world-embedding-jobs", token] : null,
    fetcher,
    { refreshInterval: 2000 }
  );
  return { jobs: data || [], error, mutate };
}
