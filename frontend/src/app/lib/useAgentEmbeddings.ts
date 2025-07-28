import useSWR from 'swr';
import { getAgentEmbeddings } from './worldEmbeddingAPI';
import { useAuth } from '../components/auth/AuthProvider';

export function useAgentEmbeddings(agentId?: number) {
  const { token } = useAuth();
  const fetcher = () => getAgentEmbeddings(agentId!, token || '');
  const { data, error, mutate } = useSWR(
    agentId && token ? ['agent-embeddings', agentId, token] : null,
    fetcher
  );
  return { embeddings: data || [], error, mutate };
}
