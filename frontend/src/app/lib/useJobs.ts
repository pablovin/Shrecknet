import useSWR from "swr";
import { listJobs } from "./jobsAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useJobs() {
  const { token } = useAuth();
  const fetcher = () => listJobs(token || "");
  const { data, error, mutate } = useSWR(
    token ? ["jobs", token] : null,
    fetcher,
    { refreshInterval: 2000 }
  );
  return { jobs: data || [], error, mutate };
}
