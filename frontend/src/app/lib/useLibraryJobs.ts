import useSWR from "swr";
import { listLibraryVectorJobs } from "./libraryAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useLibraryJobs() {
  const { token } = useAuth();
  const fetcher = () => listLibraryVectorJobs(token || "");
  const { data, error, mutate } = useSWR(
    token ? ["library-jobs", token] : null,
    fetcher,
    { refreshInterval: 2000 }
  );
  return { jobs: data || [], error, mutate };
}
