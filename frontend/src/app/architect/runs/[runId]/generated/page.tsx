"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import DashboardLayout from "@/app/components/DashboardLayout";
import AuthGuard from "@/app/components/auth/AuthGuard";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { useArchitectRun } from "@/app/lib/useArchitectRun";
import { useOntologyInstances } from "@/app/lib/useOntologyInstances";
import { ArchitectProposal } from "@/app/lib/architectAPI";
import { Loader2, ArrowLeft, CheckCircle, Edit } from "lucide-react";
import Link from "next/link";

export default function ArchitectGeneratedPage() {
  const { runId } = useParams();
  const router = useRouter();
  const { token } = useAuth();
  const { run, isLoading } = useArchitectRun(runId as string, true);

  // Fetch ontology instances to display names
  const { instances } = useOntologyInstances(run?.ontology_id ?? null);

  const instanceNameMap = useMemo(() => {
    const map = new Map<string, string>();
    instances.forEach((inst) => {
      map.set(inst.instance_id, inst.name || inst.instance_id);
    });
    return map;
  }, [instances]);

  // Filter proposals that have been generated
  const newGeneratedProposals = useMemo(() => {
    if (!run?.proposals) return [];
    return run.proposals.filter(
      (p) =>
        p.proposal_type === "new_instance" &&
        p.generated_entity_instance_id !== null &&
        p.generated_entity_instance_id !== undefined
    );
  }, [run]);

  const updatedGeneratedProposals = useMemo(() => {
    if (!run?.proposals) return [];
    return run.proposals.filter(
      (p) =>
        p.proposal_type === "update_instance" &&
        p.generated_entity_instance_id !== null &&
        p.generated_entity_instance_id !== undefined
    );
  }, [run]);

  if (isLoading) {
    return (
      <AuthGuard>
        <DashboardLayout>
          <div className="flex items-center justify-center py-20">
            <Loader2 className="animate-spin text-[var(--primary)]" size={32} />
          </div>
        </DashboardLayout>
      </AuthGuard>
    );
  }

  if (!run) {
    return (
      <AuthGuard>
        <DashboardLayout>
          <div className="max-w-7xl mx-auto py-12">
            <div className="text-center text-[var(--foreground)]/70">
              Unable to load architect run.
            </div>
          </div>
        </DashboardLayout>
      </AuthGuard>
    );
  }

  return (
    <AuthGuard>
      <DashboardLayout>
        <div className="max-w-7xl mx-auto flex flex-col gap-6">
          {/* Header */}
          <div className="bg-gradient-to-r from-[var(--primary)]/10 via-transparent to-[var(--primary)]/10 rounded-3xl border border-[var(--primary)]/20 px-6 py-8 shadow-xl">
            <div className="flex items-center gap-4 mb-4">
              <Link
                href="/architect"
                className="inline-flex items-center gap-2 text-[var(--primary)] hover:text-[var(--primary-dark)] transition"
              >
                <ArrowLeft size={20} />
                Back to Architect
              </Link>
            </div>
            <h1 className="text-3xl font-serif font-bold text-[var(--primary)] mb-2">
              Generated Instances
            </h1>
            <p className="text-[var(--foreground)]/70 max-w-3xl">
              Review the entities that were generated from approved proposals.
            </p>
            <div className="mt-4 text-sm text-[var(--foreground)]/60">
              <span className="font-semibold">Run ID:</span> {run.id}
              {" · "}
              <span className="font-semibold">Instance:</span>{" "}
              {instanceNameMap.get(run.ontology_instance_id) ||
                run.ontology_instance_id}
            </div>
          </div>

          {/* New Generated Instances */}
          {newGeneratedProposals.length > 0 && (
            <section className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-lg overflow-hidden">
              <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--border)]/60 bg-[var(--background)]/40">
                <CheckCircle className="text-green-600" size={24} />
                <h2 className="text-xl font-semibold text-[var(--primary)]">
                  New Instances Created
                </h2>
                <span className="ml-auto text-sm text-[var(--foreground)]/60">
                  {newGeneratedProposals.length}{" "}
                  {newGeneratedProposals.length === 1 ? "instance" : "instances"}
                </span>
              </div>
              <div className="divide-y divide-[var(--border)]/60">
                {newGeneratedProposals.map((proposal) => (
                  <ProposalCard
                    key={proposal.id}
                    proposal={proposal}
                    type="new"
                  />
                ))}
              </div>
            </section>
          )}

          {/* Separator between sections */}
          {newGeneratedProposals.length > 0 &&
            updatedGeneratedProposals.length > 0 && (
              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t-2 border-dashed border-[var(--border)]"></div>
                </div>
                <div className="relative flex justify-center">
                  <span className="px-4 py-1 text-sm font-medium text-[var(--foreground)]/60 bg-[var(--background)] rounded-full border border-[var(--border)]">
                    Updated Instances
                  </span>
                </div>
              </div>
            )}

          {/* Updated Generated Instances */}
          {updatedGeneratedProposals.length > 0 && (
            <section className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-lg overflow-hidden">
              <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--border)]/60 bg-[var(--background)]/40">
                <Edit className="text-blue-600" size={24} />
                <h2 className="text-xl font-semibold text-[var(--primary)]">
                  Existing Instances Updated
                </h2>
                <span className="ml-auto text-sm text-[var(--foreground)]/60">
                  {updatedGeneratedProposals.length}{" "}
                  {updatedGeneratedProposals.length === 1
                    ? "instance"
                    : "instances"}
                </span>
              </div>
              <div className="divide-y divide-[var(--border)]/60">
                {updatedGeneratedProposals.map((proposal) => (
                  <ProposalCard
                    key={proposal.id}
                    proposal={proposal}
                    type="updated"
                  />
                ))}
              </div>
            </section>
          )}

          {/* Empty state */}
          {newGeneratedProposals.length === 0 &&
            updatedGeneratedProposals.length === 0 && (
              <div className="bg-[var(--surface)] border border-[var(--border)] rounded-2xl p-12 text-center">
                <div className="text-[var(--foreground)]/60">
                  No instances have been generated yet. Approve proposals and
                  trigger generation to see results here.
                </div>
              </div>
            )}
        </div>
      </DashboardLayout>
    </AuthGuard>
  );
}

type ProposalCardProps = {
  proposal: ArchitectProposal;
  type: "new" | "updated";
};

function ProposalCard({ proposal, type }: ProposalCardProps) {
  const bgClass = type === "new" ? "bg-green-50" : "bg-blue-50";
  const iconColor = type === "new" ? "text-green-600" : "text-blue-600";

  return (
    <div className="px-6 py-5 hover:bg-[var(--background)]/30 transition">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <div
              className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-semibold ${bgClass} ${iconColor}`}
            >
              {type === "new" ? <CheckCircle size={14} /> : <Edit size={14} />}
              {type === "new" ? "Created" : "Updated"}
            </div>
            <div className="text-lg font-semibold text-[var(--primary-dark)]">
              {proposal.alias || proposal.generated_entity_instance_id}
            </div>
          </div>

          {proposal.justification && (
            <p className="text-sm text-[var(--foreground)]/70 mb-3">
              {proposal.justification}
            </p>
          )}

          <div className="grid grid-cols-2 gap-4 text-xs text-[var(--foreground)]/60">
            <div>
              <span className="font-semibold">Generated ID:</span>{" "}
              <code className="bg-[var(--background)] px-2 py-1 rounded">
                {proposal.generated_entity_instance_id}
              </code>
            </div>
            {proposal.confidence !== null && (
              <div>
                <span className="font-semibold">Confidence:</span>{" "}
                {(proposal.confidence * 100).toFixed(0)}%
              </div>
            )}
            {type === "updated" && proposal.entity_instance_id && (
              <div>
                <span className="font-semibold">Original ID:</span>{" "}
                <code className="bg-[var(--background)] px-2 py-1 rounded">
                  {proposal.entity_instance_id}
                </code>
              </div>
            )}
          </div>

          {proposal.metadata &&
            proposal.metadata.supporting_sentences &&
            Array.isArray(proposal.metadata.supporting_sentences) && (
              <details className="mt-3">
                <summary className="cursor-pointer text-sm text-[var(--primary)]/80 hover:text-[var(--primary)]">
                  Show supporting sentences
                </summary>
                <div className="mt-2 bg-[var(--background)]/70 rounded-lg p-3 text-sm text-[var(--foreground)]/70">
                  <ul className="list-disc pl-5 space-y-1">
                    {(
                      proposal.metadata.supporting_sentences as string[]
                    ).map((sentence, index) => (
                      <li key={index}>{sentence}</li>
                    ))}
                  </ul>
                </div>
              </details>
            )}
        </div>
      </div>
    </div>
  );
}
