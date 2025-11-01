import useSWR from "swr";
import { useAuth } from "../components/auth/AuthProvider";
import { listOntologyInstances, OntologyInstance } from "./ontologyInstancesAPI";

export function useOntologyInstances(ontologyId?: number | null) {
  const { token } = useAuth();
  const key =
    token && ontologyId ? ["ontologyInstances", ontologyId, token] : null;
  const { data, error, mutate, isLoading } = useSWR<OntologyInstance[]>(
    key,
    () =>
      listOntologyInstances(token || "", {
        ontology_id: ontologyId as number,
        limit: 200,
        skip: 0,
      }),
  );
  return {
    instances: data || [],
    error,
    mutate,
    isLoading,
  };
}
