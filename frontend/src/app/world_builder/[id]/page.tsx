"use client";
import { useState } from "react";
import { useParams } from "next/navigation";
import DashboardLayout from "@/app/components/DashboardLayout";
import { useAuth } from "@/app/components/auth/AuthProvider";
import useRoleRedirect from "@/app/hooks/useRoleRedirect";
import { useWorlds } from "@/app/lib/userWorlds";
import { useConcepts } from "@/app/lib/useConcept";
import { useCharacteristics } from "@/app/lib/userCharacteristic";
import { createCharacteristic, updateCharacteristic } from "@/app/lib/characteristicsAPI";
import { deleteConcept } from "@/app/lib/conceptsAPI";
import { deleteCharacteristic } from "@/app/lib/characteristicsAPI";

import WorldsSidebar from "@/app/components/world_builder/WorldSidebar";
import ConceptListSidebar from "@/app/components/world_builder/ConceptListSidebar";
import TabMenu from "@/app/components/world_builder/TabMenu";
import ConceptForm from "@/app/components/world_builder/ConceptForm";
import CharacteristicsForm from "@/app/components/world_builder/CharacteristicsForm";
import ImportConceptsModal from "@/app/components/world_builder/ImportConceptsModal";
import Image
 from "next/image";
const TAB_LIST = [
  { label: "Concepts", value: "concepts" },
  { label: "Characteristics", value: "characteristics" },
];

export default function WorldBuilderAdminPage() {
  const { user, token } = useAuth();
  const { id } = useParams();
  const { worlds, isLoading: worldsLoading, error: worldsError } = useWorlds(token);
  const currentWorld = worlds?.find(w => String(w.id) === String(id));

  const {
    concepts,
    isLoading: conceptsLoading,
    mutate: mutateConcepts,
  } = useConcepts(currentWorld?.id);

  const {
    characteristics,
    isLoading: characteristicsLoading,
    mutate: mutateCharacteristics,
  } = useCharacteristics(currentWorld?.id);



  const [activeTab, setActiveTab] = useState("concepts");
  const [selectedId, setSelectedId] = useState(null);
  const [editMode, setEditMode] = useState("edit");
  const [showImportModal, setShowImportModal] = useState(false);

  const selectedConcept = concepts.find(c => c.id === selectedId) || null;
  const selectedCharacteristic = characteristics.find(c => c.id === selectedId) || null;
  const selectedItem = activeTab === "concepts" ? selectedConcept : selectedCharacteristic;


  const enrichedConcept = editMode === "edit" && selectedConcept
  ? {
      ...selectedConcept,
      characteristic_links: selectedConcept.characteristic_links?.map(link => {
        const full = characteristics.find(c => c.id === link.characteristic_id);
        return full ? { ...link, ...full } : link;
      }) || [],

      
    }
  : null;
  

  console.log(enrichedConcept);

  const allowed = useRoleRedirect("world builder");
  if (!allowed) return null;

  if (worldsLoading) {
    return (
      <DashboardLayout>
        <div className="flex items-center justify-center min-h-[300px] text-[var(--primary)] text-2xl">
          Loading world...
        </div>
      </DashboardLayout>
    );
  }
  if (worldsError) {
    return (
      <DashboardLayout>
        <div className="flex items-center justify-center min-h-[300px] text-red-600 text-2xl">
          Error loading world(s).
        </div>
      </DashboardLayout>
    );
  }
  if (!currentWorld) {
    return (
      <DashboardLayout>
        <div className="flex items-center justify-center min-h-[300px] text-red-600 text-2xl">
          World not found.
        </div>
      </DashboardLayout>
    );
  }

  if (typeof window !== "undefined" && window.innerWidth < 900) {
    return (
      <DashboardLayout>
        <div className="flex flex-col items-center justify-center min-h-[500px] text-center">
          <span className="text-4xl mb-2">⚠️</span>
          <h1 className="text-2xl font-bold mb-1 text-[var(--primary)]">Not supported on mobile</h1>
          <p className="max-w-md">
            World Forge is a desktop-only tool.<br />
            Please use a larger screen for the best experience.
          </p>
        </div>
      </DashboardLayout>
    );
  }

  const handleCharacteristicSubmit = async (data) => {
    try {
      if (editMode === "edit" && selectedCharacteristic?.id) {
        await updateCharacteristic(selectedCharacteristic.id, data, token);
      } else {
        await createCharacteristic({ ...data, gameworld_id: currentWorld.id }, token);
      }
      mutateCharacteristics();
      setEditMode("edit");
    } catch (error) {
      console.error("Failed to save characteristic:", error);
    }
  };


  const handleDelete = async (item) => {
    try {
      if (activeTab === "concepts") {
        await deleteConcept(item.id, token);
        mutateConcepts();
      } else {
        await deleteCharacteristic(item.id, token);
        mutateCharacteristics();
      }
      setSelectedId(null);
    } catch (err) {
      console.error("Delete failed:", err);
    }
  };


  return (
    <DashboardLayout>
      <div className="flex flex-row h-[calc(100vh-64px)] w-full">
        <aside className="flex flex-col items-center -mb 10 mr-4 py-4 px-2 min-w-[88px] mt-25 max-w-[104px] bg-[var(--surface-variant)] border-r border-[var(--primary)]/10 shadow-sm h-full z-10">
          <span className="text-[15px] font-bold tracking-widest uppercase text-[var(--primary)] mb-5 mt-2 opacity-70" style={{ letterSpacing: "0.18em" }}>
            Worlds
          </span>
          <WorldsSidebar worlds={worlds} currentId={id} />
          <div className="w-2 bg-transparent" />
        </aside>

        <main className="flex-1 flex flex-col min-w-0 bg-[var(--background)]">
          <div className="flex items-center gap-4 px-0 pt-7 pb-4 border-b border-[var(--primary)]/10 bg-[var(--background)]/80">
            <Image
              src={currentWorld.logo || "/images/worlds/new_game.png"}
              alt={currentWorld.name}
              className="w-14 h-14 rounded-xl object-cover border border-[var(--primary)]/30 shadow-sm"
              width={400}
              height={400}
            />
            <div className="font-serif text-2xl font-bold text-[var(--primary)] tracking-tight">
              {currentWorld.name}
            </div>
          </div>

          <div className="flex flex-1 min-h-0 w-full">
            <section className="w-[330px] max-w-[340px] min-w-[220px] flex flex-col bg-[var(--surface-variant)] border-r border-[var(--primary)]/10 pt-0"
              style={{
                boxShadow: "0 8px 24px -14px var(--primary), 0 1.5px 0 0 var(--surface-variant)",
                minHeight: "100%",
              }}
            >
              <div className="w-full pt-4 pb-1 px-6 border-b border-[var(--primary)]/10 bg-[var(--surface-variant)]/60">
                <TabMenu
                  activeTab={activeTab}
                  onTabChange={(tab) => {
                    setActiveTab(tab);
                    setSelectedId(null);
                    setEditMode("edit");
                  }}
                  tabs={TAB_LIST}
                />
              </div>
              <ConceptListSidebar
                items={activeTab === "concepts" ? concepts : characteristics}
                type={activeTab}
                selectedId={selectedId}
                onDelete={handleDelete}
                onSelect={(item) => {
                  setSelectedId(item.id);
                  setEditMode("edit");
                }}
                onNew={() => {
                  setSelectedId(null);
                  setEditMode("new");
                }}
                onImport={activeTab === "concepts" ? () => setShowImportModal(true) : null}
                loading={activeTab === "concepts" ? conceptsLoading : characteristicsLoading}
                emptyText={`No ${activeTab} found.`}
              />
            </section>

            <section className="flex-1 flex flex-col min-w-0 bg-[var(--card-bg)]/90 p-7 overflow-y-auto">
              {activeTab === "concepts" ? (
                <ConceptForm
                  key={selectedId || "new"}
                  world={currentWorld}
                  concept={editMode === "edit" ? selectedItem : null}
                  mode={editMode}
                  initialData={editMode === "edit" ? enrichedConcept : null}
                  allCharacteristics={characteristics || []}
                  onSuccess={() => {
                    setEditMode("edit");
                    mutateConcepts();
                    mutateCharacteristics();
                  }}
                />
              ) : (
                

                <CharacteristicsForm
                  key={selectedId || "new"}
                  world={currentWorld}
                  initialData={editMode === "edit" ? selectedCharacteristic : null}
                  mode={editMode}
                  concepts={concepts || []}
                  onSubmit={handleCharacteristicSubmit}
                />
              )}
            </section>
          </div>
        </main>
      </div>
      {showImportModal && (
        <ImportConceptsModal
          currentWorld={currentWorld}
          existingConcepts={concepts}
          onClose={() => setShowImportModal(false)}
          onImport={() => {
            mutateConcepts();
            mutateCharacteristics();
          }}
        />
      )}
    </DashboardLayout>
  );
}
