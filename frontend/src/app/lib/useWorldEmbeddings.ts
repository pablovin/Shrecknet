import useSWR from "swr";
import { getWorldEmbeddings } from "./worldEmbeddingAPI";
import { useAuth } from "../components/auth/AuthProvider";

export function useWorldEmbeddings() {
  const { token } = useAuth();
  const fetcher = () => getWorldEmbeddings(token || "");
  const { data, error, mutate } = useSWR(token ? ["world_embeddings", token] : null, fetcher);
  return { embeddings: data || [], error, mutate };
}
