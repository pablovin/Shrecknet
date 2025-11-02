"use client";

import { useEffect, useMemo, useState } from "react";
import {
  startArchitectAnalysis,
  updateArchitectProposalStatus,
  ArchitectRunSummary,
  ArchitectProposal,
  ArchitectRun,
} from "../lib/architectAPI";
import DashboardLayout from "../components/DashboardLayout";
import { useWorlds } from "../lib/userWorlds";
import { useAgents } from "../lib/useAgents";
import { useArchitectRuns } from "../lib/useArchitectRuns";
import { useArchitectRun } from "../lib/useArchitectRun";
import { useOntologyInstances } from "../lib/useOntologyInstances";
import { Ontology, getOntology } from "../lib/ontologiesAPI";
import { useAuth } from "../components/auth/AuthProvider";
import { OntologyInstance } from "../lib/ontologyInstancesAPI";
import { useJobs } from "../lib/useJobs";
import { Loader2, Search, BookOpen, Play, Check, X, Calendar, Eye } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";

type Agent = {
  id: number;
  name: string;
  job: string;
  world_id: number;
  logo?: string | null;
  avatar_url?: string | null;
  description?: string | null;
  ontology_ids?: number[];
};

type BackgroundJob = {
  kind: string;
  job_id: string;
  status: string;
  progress?: number;
  description?: string;
  updated_at?: string;
};

export default function ArchitectPage() {
  const { worlds, isLoading: worldsLoading } = useWorlds();
  const [selectedWorldId, setSelectedWorldId] = useState<number | null>(null);
  const { agents, isLoading: agentsLoading } = useAgents(
    selectedWorldId ?? undefined,
  );
  const architectAgents: Agent[] = useMemo(
    () => (agents || []).filter((a: Agent) => a.job === "architect"),
    [agents],
  );
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null);
  const selectedAgent = useMemo(
    () => architectAgents.find((a) => a.id === selectedAgentId) || null,
    [architectAgents, selectedAgentId],
  );

  const { runs, mutate: mutateRuns, isLoading: runsLoading } =
    useArchitectRuns(selectedAgentId);
  const { jobs } = useJobs();

  const { token } = useAuth();
  const [ontologies, setOntologies] = useState<Ontology[]>([]);
  const [ontologiesLoading, setOntologiesLoading] = useState(false);
  const [ontologiesError, setOntologiesError] = useState<string | null>(null);

  const [selectedOntologyId, setSelectedOntologyId] = useState<number | null>(
    null,
  );
  const {
    instances,
    isLoading: instancesLoading,
    mutate: mutateInstances,
  } = useOntologyInstances(selectedOntologyId);
  const [instanceSearch, setInstanceSearch] = useState("");
  const [selectedInstanceId, setSelectedInstanceId] = useState<string | null>(
    null,
  );

  const selectedInstance: OntologyInstance | null = useMemo(() => {
    if (!selectedInstanceId) return null;
    return (
      instances.find((inst) => inst.instance_id === selectedInstanceId) || null
    );
  }, [selectedInstanceId, instances]);

  const [chunkSize, setChunkSize] = useState<string>("1200");
  const [maxChunks, setMaxChunks] = useState<string>("40");
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  const [reviewRunId, setReviewRunId] = useState<string | null>(null);
  const { run: reviewRun, mutate: mutateReviewRun, isLoading: reviewLoading } =
    useArchitectRun(reviewRunId, true);

  useEffect(() => {
    // reset agent-specific state when world changes
    setSelectedAgentId(null);
    setOntologies([]);
    setOntologiesError(null);
    setSelectedOntologyId(null);
    setSelectedInstanceId(null);
    setInstanceSearch("");
  }, [selectedWorldId]);

  useEffect(() => {
    let ignore = false;
    async function loadOntologies() {
      if (!token || !selectedAgent || !selectedAgent.ontology_ids?.length) {
        setOntologies([]);
        setSelectedOntologyId(null);
        setSelectedInstanceId(null);
        return;
      }
      setOntologiesLoading(true);
      setOntologiesError(null);
      try {
        const results = await Promise.all(
          selectedAgent.ontology_ids.map((id) => getOntology(token, id)),
        );
        if (!ignore) {
          setOntologies(results);
          setSelectedOntologyId(results[0]?.id ?? null);
          setSelectedInstanceId(null);
          setInstanceSearch("");
        }
      } catch (error) {
        if (!ignore) {
          setOntologiesError("Failed to load ontologies for this agent");
          setOntologies([]);
        }
      } finally {
        if (!ignore) setOntologiesLoading(false);
      }
    }
    loadOntologies();
    return () => {
      ignore = true;
    };
  }, [selectedAgent, token]);

  useEffect(() => {
    if (!instances.length) {
      setSelectedInstanceId(null);
      return;
    }
    if (selectedInstanceId) {
      const stillExists = instances.some(
        (inst) => inst.instance_id === selectedInstanceId,
      );
      if (!stillExists) setSelectedInstanceId(instances[0].instance_id);
    }
  }, [instances, selectedInstanceId]);

  const filteredInstances = useMemo(() => {
    const query = instanceSearch.trim().toLowerCase();
    if (!query) return instances;
    return instances.filter((inst) =>
      (inst.name || "").toLowerCase().includes(query),
    );
  }, [instances, instanceSearch]);

  const instanceNameMap = useMemo(() => {
    const map = new Map<string, string>();
    instances.forEach((inst) => {
      map.set(inst.instance_id, inst.name || inst.instance_id);
    });
    return map;
  }, [instances]);

  const architectJobs: BackgroundJob[] = useMemo(() => {
    return (jobs || []).filter((job: BackgroundJob) => job.kind === "architect_analysis");
  }, [jobs]);

  const handleSelectWorld = (worldId: number) => {
    setSelectedWorldId(worldId);
  };

  const handleRunAnalysis = async () => {
    if (!selectedAgent || !selectedInstance || !token) return;
    setAnalysisError(null);
    setLaunching(true);
    try {
      await startArchitectAnalysis(
        selectedAgent.id,
        {
          ontology_instance_id: selectedInstance.instance_id,
          ontology_id: selectedOntologyId ?? undefined,
          chunk_size: chunkSize ? Number(chunkSize) : undefined,
          max_chunks: maxChunks ? Number(maxChunks) : undefined,
        },
        token,
      );
      await Promise.all([mutateRuns(), mutateInstances()]);
    } catch (err: any) {
      const message = typeof err === "string" ? err : "Failed to start analysis";
      setAnalysisError(message);
    } finally {
      setLaunching(false);
    }
  };

  const jobForRun = (run: ArchitectRunSummary) => {
    if (!run.background_job_id) return null;
    return architectJobs.find(
      (job) => Number(job.job_id) === Number(run.background_job_id),
    );
  };

  const handleProposalStatusChange = async (
    proposalId: string,
    status: "approved" | "rejected" | "pending",
  ) => {
    if (!token || !reviewRunId) return;
    await updateArchitectProposalStatus(reviewRunId, [proposalId], status, token);
    await Promise.all([mutateReviewRun(), mutateRuns()]);
  };

  const renderWorldSelection = () => {
    if (selectedWorldId) {
      const world = worlds.find((w: any) => w.id === selectedWorldId);
      if (!world) return null;
      return (
        <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl p-5 flex flex-col gap-3 h-full shadow-lg">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-[var(--primary)]">
              1. World Selected
            </h2>
          <button
            onClick={() => setSelectedWorldId(null)}
              className="text-sm text-[var(--primary)]/70 hover:text-[var(--primary)]"
            >
              Change
            </button>
          </div>
          <div className="flex items-center gap-4">
            <div className="relative h-16 w-16 rounded-xl overflow-hidden border border-[var(--border)]">
              <Image
                src={world.logo || "/images/worlds/new_game.png"}
                alt={world.name}
                fill
                className="object-cover"
              />
            </div>
            <div>
              <div className="text-xl font-bold text-[var(--primary-dark)]">
                {world.name}
              </div>
              <div className="text-sm text-[var(--foreground)]/70">
                ID: {world.id}
              </div>
            </div>
          </div>
        </div>
      );
    }

    if (worldsLoading) {
      return (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="animate-spin text-[var(--primary)]" size={28} />
        </div>
      );
    }

    if (!worlds.length) {
      return (
        <div className="py-12 text-center text-[var(--foreground)]/70">
          You do not have any worlds yet.
        </div>
      );
    }

    return (
      <div className="flex flex-col gap-4">
        <h2 className="text-lg font-semibold text-[var(--primary)]">
          1. Choose a World
        </h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {worlds.map((world: any) => (
            <button
              key={world.id}
              onClick={() => handleSelectWorld(world.id)}
              className="group text-left bg-[var(--surface)] border border-[var(--border)] hover:border-[var(--primary)] rounded-2xl p-4 shadow transition"
            >
              <div className="flex items-center gap-4">
                <div className="relative h-16 w-16 rounded-xl overflow-hidden border border-[var(--border)]">
                  <Image
                    src={world.logo || "/images/worlds/new_game.png"}
                    alt={world.name}
                    fill
                    className="object-cover"
                  />
                </div>
                <div>
                  <div className="text-lg font-semibold text-[var(--primary-dark)]">
                    {world.name}
                  </div>
                  <div className="text-xs text-[var(--foreground)]/70">
                    Click to work with this world
                  </div>
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>
    );
  };

  const renderAgentSelection = () => {
    if (!selectedWorldId) {
      return (
        <div className="bg-[var(--surface)] border border-dashed border-[var(--border)] rounded-2xl p-6 h-full flex items-center justify-center text-sm text-[var(--foreground)]/60">
          Select a world to pick an Architect agent.
        </div>
      );
    }

    if (agentsLoading) {
      return (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="animate-spin text-[var(--primary)]" size={28} />
        </div>
      );
    }

    if (!architectAgents.length) {
      return (
        <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl p-6 text-sm text-[var(--foreground)]/70 h-full flex flex-col justify-center">
          <div className="font-semibold text-[var(--primary)] mb-2">
            2. Select an Architect Agent
          </div>
          <p>
            This world does not have any Architect agents yet. Create an Architect agent in the Agents settings first.
          </p>
        </div>
      );
    }

    return (
      <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl p-6 h-full shadow-lg">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-[var(--primary)]">
            2. Select an Architect Agent
          </h2>
          {selectedAgent && (
            <span className="text-xs uppercase tracking-wide text-[var(--primary)]/70">Ready</span>
          )}
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          {architectAgents.map((agent) => {
            const isActive = agent.id === selectedAgentId;
            return (
              <button
                key={agent.id}
                onClick={() => setSelectedAgentId(agent.id)}
                className={`flex items-center gap-4 rounded-xl border px-4 py-3 text-left transition bg-[var(--background)]/60 ${
                  isActive
                    ? "border-[var(--primary)] shadow-lg"
                    : "border-[var(--border)] hover:border-[var(--primary)]/60"
                }`}
              >
                <div className="relative h-14 w-14 rounded-full overflow-hidden border border-[var(--border)]">
                  <Image
                    src={
                      agent.logo ||
                      agent.avatar_url ||
                      "/uploads/default/avatars/logo.png"
                    }
                    alt={agent.name}
                    fill
                    className="object-cover"
                  />
                </div>
                <div className="flex-1">
                  <div className="text-base font-semibold text-[var(--primary-dark)]">
                    {agent.name}
                  </div>
                  <div className="text-xs text-[var(--foreground)]/60">
                    Ontologies: {agent.ontology_ids?.length || 0}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    );
  };

  const renderTomePanel = () => {
    if (!selectedAgent) return null;
    return (
      <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl p-6 shadow-lg flex flex-col h-full">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-[var(--primary)]">
            Select the Tome
          </h3>
          <BookOpen className="text-[var(--primary)]" size={20} />
        </div>
        <p className="text-sm text-[var(--foreground)]/60 mt-1">
          Choose an ontology and instance for the Architect to analyse.
        </p>
        <div className="mt-4">
          <label className="text-xs uppercase tracking-wide text-[var(--primary)]/70">
            Ontology
          </label>
          <select
            value={selectedOntologyId ?? ""}
            onChange={(event) => {
              const value = event.target.value;
              setSelectedOntologyId(value ? Number(value) : null);
              setSelectedInstanceId(null);
            }}
            className="w-full mt-1 px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--background)]/70 text-sm"
            disabled={ontologiesLoading}
          >
            {ontologies.length === 0 && (
              <option value="">No ontologies available</option>
            )}
            {ontologies.map((ontology) => (
              <option key={ontology.id} value={ontology.id}>
                {ontology.name}
              </option>
            ))}
          </select>
          {ontologiesError && (
            <div className="mt-2 text-xs text-red-500">{ontologiesError}</div>
          )}
        </div>
        <div className="mt-4">
          <label className="text-xs uppercase tracking-wide text-[var(--primary)]/70">
            Search Instance
          </label>
          <div className="mt-1 flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 bg-[var(--background)]/60">
            <Search size={16} className="text-[var(--foreground)]/60" />
            <input
              value={instanceSearch}
              onChange={(event) => setInstanceSearch(event.target.value)}
              placeholder="Search by name"
              className="flex-1 bg-transparent text-sm outline-none"
            />
          </div>
        </div>
        <div className="mt-4 space-y-2 overflow-y-auto max-h-72 pr-1">
          {instancesLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="animate-spin text-[var(--primary)]" size={24} />
            </div>
          ) : filteredInstances.length === 0 ? (
            <div className="py-6 text-sm text-[var(--foreground)]/60 text-center">
              No ontology instances found.
            </div>
          ) : (
            filteredInstances.map((instance) => {
              const active = instance.instance_id === selectedInstanceId;
              return (
                <button
                  key={instance.instance_id}
                  onClick={() => setSelectedInstanceId(instance.instance_id)}
                  className={`w-full text-left rounded-xl border px-4 py-3 transition ${
                    active
                      ? "border-[var(--primary)] bg-[var(--primary)]/5"
                      : "border-[var(--border)] hover:border-[var(--primary)]/60"
                  }`}
                >
                  <div className="text-sm font-semibold text-[var(--primary-dark)]">
                    {instance.name || instance.instance_id}
                  </div>
                  <div className="text-xs text-[var(--foreground)]/60 mt-1">
                    Instance ID: {instance.instance_id}
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>
    );
  };

  const renderAnalysisPanel = () => {
    if (!selectedAgent) return null;
    return (
      <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl p-6 shadow-lg flex flex-col h-full">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-[var(--primary)]">
            Shape the Analysis
          </h3>
          <Play className="text-[var(--primary)]" size={20} />
        </div>
        <p className="text-sm text-[var(--foreground)]/60 mt-1">
          Configure chunking parameters before launching the Architect.
        </p>
        <div className="mt-4 grid grid-cols-1 gap-4">
          <div>
            <label className="text-xs uppercase tracking-wide text-[var(--primary)]/70">
              Chunk Size (characters)
            </label>
            <input
              type="number"
              min={200}
              value={chunkSize}
              onChange={(event) => setChunkSize(event.target.value)}
              className="mt-1 w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--background)]/70 text-sm"
            />
          </div>
          <div>
            <label className="text-xs uppercase tracking-wide text-[var(--primary)]/70">
              Max Chunks
            </label>
            <input
              type="number"
              min={1}
              value={maxChunks}
              onChange={(event) => setMaxChunks(event.target.value)}
              className="mt-1 w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--background)]/70 text-sm"
            />
          </div>
        </div>
        {selectedInstance ? (
          <div className="mt-4 text-sm text-[var(--foreground)]/70">
            <span className="font-semibold text-[var(--primary-dark)]">
              Selected instance:
            </span>{" "}
            {selectedInstance.name || selectedInstance.instance_id}
          </div>
        ) : (
          <div className="mt-4 text-sm text-red-500">
            Select an ontology instance to enable analysis.
          </div>
        )}
        {analysisError && (
          <div className="mt-3 text-sm text-red-500">{analysisError}</div>
        )}
        <button
          onClick={handleRunAnalysis}
          disabled={!selectedInstance || launching}
          className="mt-auto inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--primary)] text-white font-semibold px-4 py-3 shadow hover:bg-[var(--primary-dark)] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {launching ? <Loader2 className="animate-spin" size={18} /> : <Play size={18} />}
          {launching ? "Launching..." : "Start Architect Analysis"}
        </button>
      </div>
    );
  };

  const renderJourneyRow = (run: ArchitectRunSummary) => {
    const job = jobForRun(run);
    const statusBadge = (() => {
      switch (run.status) {
        case "completed":
          return "bg-green-100 text-green-700";
        case "failed":
          return "bg-red-100 text-red-600";
        case "running":
          return "bg-blue-100 text-blue-600";
        default:
          return "bg-gray-100 text-gray-600";
      }
    })();
    return (
      <tr
        key={run.id}
        className="border-b border-[var(--border)]/60 hover:bg-[var(--surface)]"
      >
        <td className="px-4 py-3 text-sm text-[var(--foreground)]/80">
          <div className="font-semibold text-[var(--primary-dark)]">
            {instanceNameMap.get(run.ontology_instance_id) ||
              run.ontology_instance_id}
          </div>
          <div className="text-xs text-[var(--foreground)]/60">
            Run ID: {run.id.slice(0, 8)}
          </div>
        </td>
        <td className="px-4 py-3 text-sm text-[var(--foreground)]/70">
          <div className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-semibold ${statusBadge}`}>
            {run.status}
          </div>
          {job && (
            <div className="text-xs text-[var(--foreground)]/60 mt-1">
              Job {job.status}
              {job.progress !== undefined && job.progress !== null && (
                <span> · {(job.progress * 100).toFixed(0)}%</span>
              )}
            </div>
          )}
        </td>
        <td className="px-4 py-3 text-sm text-[var(--foreground)]/70">
          <div className="flex items-center gap-2 text-xs text-[var(--foreground)]/60">
            <Calendar size={14} />
            {formatDistanceToNow(new Date(run.created_at), { addSuffix: true })}
          </div>
        </td>
        <td className="px-4 py-3 text-right">
          <div className="flex items-center justify-end gap-2">
            <button
              onClick={() => setReviewRunId(run.id)}
              disabled={run.status !== "completed"}
              className="inline-flex items-center gap-2 rounded-lg border border-[var(--primary)] px-3 py-1.5 text-sm text-[var(--primary)] hover:bg-[var(--primary)] hover:text-white transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Review proposals
            </button>
            <Link
              href={`/architect/runs/${run.id}/generated`}
              className="inline-flex items-center gap-2 rounded-lg border border-[var(--primary)]/60 px-3 py-1.5 text-sm text-[var(--primary)]/80 hover:bg-[var(--primary)]/10 hover:text-[var(--primary)] transition"
            >
              <Eye size={16} />
              View Generated
            </Link>
          </div>
        </td>
      </tr>
    );
  };

  return (
    <DashboardLayout>
      <div className="max-w-7xl mx-auto flex flex-col gap-6">
        <div className="bg-gradient-to-r from-[var(--primary)]/10 via-transparent to-[var(--primary)]/10 rounded-3xl border border-[var(--primary)]/20 px-6 py-8 shadow-xl">
          <h1 className="text-3xl font-serif font-bold text-[var(--primary)] mb-2">
            Architect Workspace
          </h1>
          <p className="text-[var(--foreground)]/70 max-w-3xl">
            Guide the Architect through your world&apos;s tomes. Select a world, choose an Architect agent, pick an ontology instance, and launch the background analysis. Monitor the journeys below and review proposals in a full-screen workspace once they are ready.
          </p>
        </div>

        <section className="grid gap-6 lg:grid-cols-2">
          {renderWorldSelection()}
          {renderAgentSelection()}
        </section>

        {selectedAgent && (
          <section className="grid gap-6 lg:grid-cols-2">
            {renderTomePanel()}
            {renderAnalysisPanel()}
          </section>
        )}

        {selectedAgent && (
          <section className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-lg overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border)]/60">
              <h3 className="text-lg font-semibold text-[var(--primary)]">
                Architect Journeys
              </h3>
              <span className="text-xs text-[var(--foreground)]/60">
                Auto-refreshing every few seconds
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full">
                <thead className="bg-[var(--background)]/40 text-xs uppercase text-[var(--foreground)]/60">
                  <tr>
                    <th className="px-4 py-3 text-left">Tome</th>
                    <th className="px-4 py-3 text-left">Status</th>
                    <th className="px-4 py-3 text-left">Created</th>
                    <th className="px-4 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {runsLoading ? (
                    <tr>
                      <td colSpan={4} className="px-4 py-12 text-center">
                        <Loader2 className="mx-auto animate-spin text-[var(--primary)]" size={28} />
                      </td>
                    </tr>
                  ) : runs.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-4 py-12 text-center text-sm text-[var(--foreground)]/60">
                        No journeys yet. Start an analysis to populate this list.
                      </td>
                    </tr>
                  ) : (
                    runs.map((run) => renderJourneyRow(run))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {reviewRunId && (
          <ReviewDialog
            run={reviewRun}
            loading={reviewLoading}
            onClose={() => setReviewRunId(null)}
            onStatusChange={handleProposalStatusChange}
          />
        )}
      </div>
    </DashboardLayout>
  );
}

type ReviewDialogProps = {
  run?: ArchitectRun | null;
  loading: boolean;
  onClose: () => void;
  onStatusChange: (
    proposalId: string,
    status: "approved" | "rejected" | "pending",
  ) => Promise<void>;
};

function ReviewDialog({ run, loading, onClose, onStatusChange }: ReviewDialogProps) {
  const [filter, setFilter] = useState<"all" | "new" | "existing">("all");

  const proposals: ArchitectProposal[] = useMemo(() => {
    if (!run?.proposals) return [];
    if (filter === "all") return run.proposals;
    if (filter === "new")
      return run.proposals.filter((p) => p.proposal_type === "new_instance");
    return run.proposals.filter((p) => p.proposal_type === "update_instance");
  }, [run, filter]);

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex flex-col">
      <div className="relative max-w-6xl mx-auto w-full bg-[var(--background)] h-full lg:my-6 lg:rounded-3xl overflow-hidden shadow-2xl border border-[var(--primary)]/20">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border)]/60 bg-[var(--surface)]">
          <div>
            <h3 className="text-2xl font-semibold text-[var(--primary)]">
              Review Architect Proposals
            </h3>
            {run && (
              <p className="text-sm text-[var(--foreground)]/60">
                Instance: {run.ontology_instance_id} · Run {run.id}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded-full border border-[var(--border)] p-2 text-[var(--foreground)]/70 hover:text-[var(--primary)] hover:border-[var(--primary)]"
            aria-label="Close review"
          >
            <X size={20} />
          </button>
        </div>
        <div className="px-6 py-4 border-b border-[var(--border)]/60 bg-[var(--surface)]/60 flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-[var(--foreground)]/60">Show:</span>
            <button
              onClick={() => setFilter("all")}
              className={`rounded-full px-3 py-1 text-xs font-semibold border ${
                filter === "all"
                  ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                  : "border-[var(--border)] text-[var(--foreground)]/60"
              }`}
            >
              All
            </button>
            <button
              onClick={() => setFilter("new")}
              className={`rounded-full px-3 py-1 text-xs font-semibold border ${
                filter === "new"
                  ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                  : "border-[var(--border)] text-[var(--foreground)]/60"
              }`}
            >
              New Instances
            </button>
            <button
              onClick={() => setFilter("existing")}
              className={`rounded-full px-3 py-1 text-xs font-semibold border ${
                filter === "existing"
                  ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                  : "border-[var(--border)] text-[var(--foreground)]/60"
              }`}
            >
              Existing Instances
            </button>
          </div>
          {run && (
            <div className="text-xs text-[var(--foreground)]/60">
              Created {formatDistanceToNow(new Date(run.created_at), { addSuffix: true })}
            </div>
          )}
        </div>
        <div className="overflow-y-auto h-full px-6 py-6">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="animate-spin text-[var(--primary)]" size={32} />
            </div>
          ) : !run ? (
            <div className="py-20 text-center text-sm text-[var(--foreground)]/60">
              Unable to load run details.
            </div>
          ) : proposals.length === 0 ? (
            <div className="py-20 text-center text-sm text-[var(--foreground)]/60">
              No proposals match this filter.
            </div>
          ) : (
            <div className="grid gap-4">
              {proposals.map((proposal) => {
                const isNew = proposal.proposal_type === "new_instance";
                return (
                  <div
                    key={proposal.id}
                    className="border border-[var(--border)] rounded-2xl p-5 bg-[var(--surface)]"
                  >
                    <div className="flex flex-wrap justify-between gap-2">
                      <div>
                        <div className="text-sm uppercase tracking-wide text-[var(--primary)]/70">
                          {isNew ? "New Instance" : "Existing Instance"}
                        </div>
                        <div className="text-xl font-semibold text-[var(--primary-dark)]">
                          {proposal.alias || proposal.entity_instance_id || proposal.id}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <StatusBadge status={proposal.status} />
                        {proposal.confidence !== null && (
                          <div className="text-xs text-[var(--foreground)]/60">
                            Confidence {(proposal.confidence * 100).toFixed(0)}%
                          </div>
                        )}
                      </div>
                    </div>
                    {proposal.justification && (
                      <p className="mt-3 text-sm text-[var(--foreground)]/70">
                        {proposal.justification}
                      </p>
                    )}
                    {proposal.metadata && proposal.metadata.supporting_sentences && (
                      <div className="mt-3 bg-[var(--background)]/70 rounded-xl p-3 text-sm text-[var(--foreground)]/70">
                        <div className="font-semibold text-[var(--primary-dark)] mb-1">
                          Supporting sentences
                        </div>
                        <ul className="list-disc pl-5 space-y-1">
                          {(proposal.metadata.supporting_sentences as string[]).map((sentence, index) => (
                            <li key={index}>{sentence}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {proposal.evidence && proposal.evidence.length > 0 && (
                      <details className="mt-3">
                        <summary className="cursor-pointer text-sm text-[var(--primary)]/80 hover:text-[var(--primary)]">
                          Show evidence
                        </summary>
                        <div className="mt-2 space-y-2 text-sm text-[var(--foreground)]/70">
                          {proposal.evidence.map((item, index) => (
                            <div
                              key={index}
                              className="rounded-lg border border-[var(--border)] px-3 py-2 bg-[var(--background)]/60"
                            >
                              {item.text && (
                                <div className="mb-2">
                                  <div className="text-xs uppercase text-[var(--primary)]/60">
                                    Chunk {item.chunk_index}
                                  </div>
                                  <div>{item.text as string}</div>
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                    <div className="mt-4 flex flex-wrap gap-2">
                      <button
                        onClick={() => onStatusChange(proposal.id, "approved")}
                        className="inline-flex items-center gap-2 rounded-lg border border-green-500 text-green-600 px-3 py-1.5 text-sm hover:bg-green-500/10"
                      >
                        <Check size={16} /> Approve
                      </button>
                      <button
                        onClick={() => onStatusChange(proposal.id, "rejected")}
                        className="inline-flex items-center gap-2 rounded-lg border border-red-500 text-red-500 px-3 py-1.5 text-sm hover:bg-red-500/10"
                      >
                        <X size={16} /> Reject
                      </button>
                      {proposal.status !== "pending" && (
                        <button
                          onClick={() => onStatusChange(proposal.id, "pending")}
                          className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] text-[var(--foreground)]/70 px-3 py-1.5 text-sm hover:bg-[var(--background)]/60"
                        >
                          Reset
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  let styles = "bg-gray-100 text-gray-600";
  let label = status;
  if (status === "approved") styles = "bg-green-100 text-green-700";
  if (status === "rejected") styles = "bg-red-100 text-red-600";
  if (status === "pending") styles = "bg-blue-100 text-blue-600";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs font-semibold ${styles}`}>
      {status === "approved" && <Check size={12} />}
      {status === "rejected" && <X size={12} />}
      {label}
    </span>
  );
}
