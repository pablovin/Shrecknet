"use client";

import { useState, useEffect } from "react";
import AuthGuard from "@/app/components/auth/AuthGuard";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useAuth } from "@/app/components/auth/AuthProvider";
import { useParams, useRouter } from "next/navigation";
import { updatePage, deletePage } from "@/app/lib/pagesAPI";
import { usePageById } from "@/app/lib/usePageById";
import { useWorld } from "@/app/lib/useWorld";
import { useWorlds } from "@/app/lib/userWorlds";
import { useConceptById } from "@/app/lib/useConceptById";
import { useConcepts } from "@/app/lib/useConcept";
import { useCharacteristicsForConcept } from "@/app/lib/useCharacteristicsForConcept";
import { usePages } from "@/app/lib/usePage";
import PageTitleBar from "@/app/components/see_page/PageTitleBar";
import TitleSection from "@/app/components/see_page/TitleSection";
import HeaderSection from "@/app/components/see_page/HeaderSection";
import BodySection from "@/app/components/see_page/BodySection";
import EditableContent from "@/app/components/editor/EditableContent";
import WorldBreadcrumb from "@/app/components/worlds/WorldBreadCrump";
import useRoleRedirect from "@/app/hooks/useRoleRedirect";
import { useTranslation } from "@/app/hooks/useTranslation";
import { hasRole } from "@/app/lib/roles";
import ModalContainer from "@/app/components/template/modalContainer";
import {
  ChevronLeft,
  ChevronRight,
  ScrollText,
  Brain,
  Calendar,
  Map,
} from "lucide-react";
import CreatePageForm from "../../../../../../components/create_page/CreatePageForm";
import PageTabMenu from "@/app/components/see_page/PageTabMenu";
import EventTimeline from "@/app/components/see_page/EventTimeline";
import RelationshipMapTab from "@/app/components/see_page/RelationshipMapTab";
import Changelog from "@/app/components/see_page/Changelog";
import { useAgents } from "@/app/lib/useAgents";

// --- Collapsible Sidebar, grid-based, NOT fixed ---
function CollapsibleDetails({ open, setOpen, children }) {
  return (
    <div
      className={`transition-all duration-300 h-full ${
        open ? "w-80 min-w-[270px]" : "w-9 min-w-[36px] max-w-[36px]"
      } bg-[var(--surface)] border border-[var(--border)] rounded-2xl shadow-xl overflow-hidden flex flex-col`}
    >
      <div className="flex flex-row h-full">
        <button
          className="flex flex-col items-center justify-center px-1 cursor-pointer hover:bg-[var(--surface-variant)] border-r border-[var(--border)]"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
          <span
            className={`mt-1 font-semibold text-xs transition-opacity ${
              open ? "opacity-0" : "opacity-100"
            }`}
            style={{ writingMode: "vertical-rl", textOrientation: "mixed" }}
          >
            Details
          </span>
        </button>
        <div className={`transition-all duration-300 flex-1 overflow-y-auto ${open ? "opacity-100" : "opacity-0 pointer-events-none"}`}>
          {open && <div className="p-3">{children}</div>}
        </div>
      </div>
    </div>
  );
}

export default function PageView() {
  const { user, token } = useAuth();
  const { t } = useTranslation();
  const router = useRouter();
  const params = useParams();
  const pageId = Number(params?.pageID);
    

  const [showEditModal, setShowEditModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // For sidebar open/collapse
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [activeTab, setActiveTab] = useState<
    "description" | "notes" | "events" | "relationships"
  >("description");
  const [showChangelog, setShowChangelog] = useState(false);
  const [imgError, setImgError] = useState(false);

  

  const { page, mutate: mutatePage, isLoading: pageLoading } = usePageById(pageId);
  const { concept, isLoading: conceptLoading } = useConceptById(page?.concept_id);
  const { world, isLoading: worldLoading } = useWorld(page?.gameworld_id);
  const { worlds, isLoading: worldsLoading } = useWorlds();
  const { concepts, isLoading: conceptsLoading } = useConcepts(world?.id);
  const { characteristics, isLoading: charsLoading } = useCharacteristicsForConcept(page?.concept_id);
  const { pages: worldPages } = usePages(world?.id ? { gameworld_id: world.id } : {});

  const { agents } = useAgents(page?.gameworld_id);
  const chatAgent = agents?.find(a => a.task === "conversational");

  const loading = pageLoading || conceptLoading || worldLoading || worldsLoading || conceptsLoading || charsLoading;

  // If there is no main content, default to showing the Notes tab
  useEffect(() => {
    if (
      page &&
      (!page.content || page.content.trim() === "") &&
      activeTab === "description"
    ) {
      setActiveTab("notes");
    }
  }, [page]);


  const getSectionValues = (type) => {
    if (!characteristics || !page?.values) return [];
    return characteristics
      .filter(c => c.display_type === type)
      .map(c => {
        const v = page.values.find(val => val.characteristic_id === c.id);
        return { characteristic: c, value: v?.value };
      });
  };

  const groups = [...new Set(concepts.filter(c => !!c.group).map(c => c.group))];
  const agent = agents?.find(a => a.id === page?.updated_by_agent_id);

  function openChatWithAgent() {
    if (!chatAgent) return;
    const detail = {
      agentId: chatAgent.id,
      input: `I have the following questions about the page ${page?.name}: `,
    };
    window.dispatchEvent(new CustomEvent("openChatPanel", { detail }));
  }

  const allowed = useRoleRedirect("player");
  if (!allowed) return null;

  async function handleSaveContent(newContent) {
    if (!pageId || !token) return;

    try {
      await updatePage(Number(pageId), { content: newContent }, token);
      mutatePage();
    } catch (err) {
      console.error("Failed to save page content", err);
    }
  }

  async function handleSaveNotes(newContent) {
    if (!pageId || !token) return;

    try {
      await updatePage(Number(pageId), { autogenerated_content: newContent }, token);
      mutatePage();
    } catch (err) {
      console.error("Failed to save notes", err);
    }
  }

  // --- DELETE PAGE HANDLER ---
  async function handleDeletePage() {
    if (!pageId || !token) return;
    setDeleting(true);
    try {
      await deletePage(pageId, token);
      setShowDeleteModal(false);
      // Redirect to concept page list after delete
      if (world && concept) {
        router.push(`/worlds/${world.id}/concept/${concept.id}`);
      } else {
        router.push(`/worlds`);
      }
    } catch (err) {
      setDeleting(false);
      alert("Failed to delete page: " + (err?.message || "Unknown error"));
    }
  }



  function filterNonEmptySectionValues(values) {
    return values.filter(
      ({ value }) =>
        value !== null &&
        value !== undefined &&
        (typeof value === "string" ? value.trim() !== "" : true) &&
        !(Array.isArray(value) && value.length === 0)
    );
  }


const titleSectionValues = filterNonEmptySectionValues(getSectionValues("title"));
const bodySectionValues = filterNonEmptySectionValues(getSectionValues("body"));

  return (
    <AuthGuard>
      {world && concept && concepts.length > 0 && (
        <WorldBreadcrumb
          world={world}
          allWorlds={worlds}
          currentConcept={concept}
          concepts={concepts}
          groups={groups}
          onWorldSelect={(w) => router.push(`/worlds/${w.id}`)}
          onConceptGroupSelect={(type, value) => {
            if (type === "concept") {
              router.push(`/worlds/${world.id}/concept/${value.id}`);
            } else {
              router.push(`/worlds/${world.id}/group/${value}`);
            }
          }}
          currentPageName={page?.name}
        />
      )}

      <DashboardLayout>
        <div className="min-h-screen w-full bg-[var(--background)] text-[var(--foreground)] transition-colors duration-300 px-2 sm:px-6 py-4">
          {loading ? (
            <div className="text-xl font-semibold">Loading...</div>
          ) : page ? (
            <div className="max-w-6xl mx-auto">
              {/* Tighter gap, stack controls compactly */}
              <div className="flex flex-col gap-2">

                <PageTitleBar
                  logo={page?.logo || "/images/default/pages/logo.png"}
                  pageName={page?.name}
                  settings={{
                    allowCrosslinks: page.allow_crosslinks,
                    ignoreCrosslink: page.ignore_crosslink,
                    allowCrossworld: page.allow_crossworld,
                    canEdit: hasRole(user?.role, "writer"),
                    onEdit: () => setShowEditModal(true),
                  }}
                  conceptName={concept?.name}
                  conceptLogo={concept?.logo}
                  canDelete={hasRole(user?.role, "world builder")}
                  onDelete={() => setShowDeleteModal(true)}
                  deleteLabel={t("delete_page")}
                  onShowChangelog={() => setShowChangelog(true)}
                />
              </div>

              {/* --- Main content & details area, responsive grid --- */}
              <div className="mt-1 grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-4 relative">
                {/* --- Main Content Column --- */}
                <div className="flex flex-col gap-2 min-w-0">

                  {titleSectionValues.length > 0 && (
                    <TitleSection
                      values={titleSectionValues}
                      worldId={world?.id}                      
                    />
                  )}

                  
                    {bodySectionValues.length > 0 && (
                      <BodySection values={bodySectionValues} worldId={world?.id} conceptid={concept?.id} />
                    )}
                    
                  <PageTabMenu
                    activeTab={activeTab}
                    onTabChange={(tab) =>
                      setActiveTab(
                        tab as "description" | "notes" | "events" | "relationships"
                      )
                    }
                    tabs={[
                      { value: "description", label: "tab_description", icon: ScrollText },
                      { value: "notes", label: "tab_writer_notes", icon: Brain, ai: true },
                      { value: "events", label: "tab_key_events", icon: Calendar },
                      { value: "relationships", label: "tab_relationships", icon: Map },
                    ]}
                  />
                  {activeTab === "description" ? (
                    <EditableContent
                      id="world-content-editor"
                      content={page.content}
                      canEdit={hasRole(user?.role, "writer")}
                      onSaveContent={handleSaveContent}
                      pageType="worlds"
                      pageName={page.name}
                      className="w-full min-h-[300px] text-base md:text-lg prose prose-invert max-w-none"
                    />
                  ) : activeTab === "notes" ? (
                    <div className="w-full">
                      {agent && (
                        <div className="flex items-start gap-3 mb-4 p-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] shadow-sm">
                          <img
                            src={imgError ? "/images/default/avatars/logo.png" : agent.logo || "/images/default/avatars/logo.png"}
                            alt={agent.name}
                            className="w-36 h-36 rounded-full border border-[var(--border)]"
                            onError={() => setImgError(true)}
                          />
                          <p className="text-sm leading-snug text-[var(--foreground)]">
                            <span className="font-semibold">{agent.name}</span> penned these notes after carefully reading some of the material on this site, hoping his insights spark inspiration and enrich your adventures.
                          </p>
                        </div>
                      )}
                      <EditableContent
                        id="autogenerated-content-editor"
                        content={page.autogenerated_content}
                        canEdit={hasRole(user?.role, "writer")}
                        onSaveContent={handleSaveNotes}
                        pageType="worlds"
                        pageName={page.name}
                        className="w-full min-h-[300px] text-base md:text-lg prose prose-invert max-w-none"
                      />
                    </div>
                  ) : activeTab === "events" ? (
                    <EventTimeline
                      pageId={page.id}
                      events={page.key_events || []}
                      pages={worldPages}
                      onUpdated={mutatePage}
                    />
                  ) : activeTab === "relationships" ? (
                    <RelationshipMapTab page={page} pages={worldPages} />
                  ) : null}
                </div>

                {/* --- Details Sidebar --- */}
                {getSectionValues("header").length > 0 && (
                  <CollapsibleDetails open={sidebarOpen} setOpen={setSidebarOpen}>
                    <HeaderSection values={getSectionValues("header")} worldId={world?.id} conceptid={concept?.id} />
                  </CollapsibleDetails>
                )}
              </div>
            </div>
          ) : (
            <div className="text-lg text-red-500">Page not found.</div>
          )}

          {/* --- Edit Modal --- */}
          {showEditModal && page && (
            <ModalContainer
              title="Edit Page Settings"
              onClose={() => setShowEditModal(false)}
              className="max-w-2xl"
            >
              <CreatePageForm
                selectedWorld={world}
                selectedConcept={concept}
                token={token}
                initialValues={page}
                mode="edit"
                onSubmit={async (payload) => {
                  await updatePage(page.id, payload, token);
                  setShowEditModal(false);
                  mutatePage();
                }}
                onSuccess={() => setShowEditModal(false)}
              />
            </ModalContainer>
          )}

          {/* --- Delete Confirmation Modal --- */}
          {showDeleteModal && (
            <ModalContainer
              title={t("delete_page")}
              onClose={() => setShowDeleteModal(false)}
              className="max-w-md"
            >
              <div className="text-center py-4">
                <p className="text-lg font-semibold text-red-600 mb-3">
                  {t("delete_page_confirm")}
                </p>
                <p className="text-sm text-[var(--foreground)]/80 mb-6">
                  {t("delete_page_warning")}
                </p>
                <div className="flex justify-center gap-4">
                  <button
                    onClick={handleDeletePage}
                    disabled={deleting}
                    className="px-5 py-2 rounded-full bg-red-500 text-white font-bold hover:bg-red-600 transition"
                  >
                    {deleting ? t("deleting") : t("yes_delete")}
                  </button>
                  <button
                    onClick={() => setShowDeleteModal(false)}
                    className="px-5 py-2 rounded-full bg-[var(--primary)] text-white font-bold hover:bg-[var(--accent)] transition"
                  >
                    {t("cancel")}
                  </button>
                </div>
              </div>
            </ModalContainer>
          )}

          {showChangelog && (
            <ModalContainer
              title={t("tab_changelog")}
              onClose={() => setShowChangelog(false)}
              className="max-w-2xl"
            >
              <Changelog changes={page.changelog || []} />
            </ModalContainer>
          )}
        </div>
        {chatAgent && (
          <button
            onClick={openChatWithAgent}
            className="fixed bottom-6 left-6 z-40 flex flex-col items-center group"
          >
            <img
              src={chatAgent.logo || "/images/default/avatars/logo.png"}
              alt={chatAgent.name}
              className="w-16 h-16 rounded-full border-2 border-[var(--primary)] shadow-lg group-hover:scale-110 transition-transform object-cover"
            />
            <span className="mt-1 px-2 py-0.5 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] text-xs font-semibold shadow group-hover:bg-[var(--accent)]">
              Ask {chatAgent.name}
            </span>
          </button>
        )}
      </DashboardLayout>
    </AuthGuard>
  );
}
