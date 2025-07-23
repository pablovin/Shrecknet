"use client";

import Image from "next/image";
import { useState } from "react";
import ModalContainer from "../template/modalContainer";
import SettingsDisplay from "./SettingsDisplay"; // Update the path if needed

export default function PageTitleBar({
  pageName,
  logo,
  settings, // { allowCrosslinks, ignoreCrosslink, allowCrossworld, onEdit, canEdit }
  conceptName,
  conceptLogo,
  onDelete,
  canDelete,
  deleteLabel,
}) {
  const [zoomOpen, setZoomOpen] = useState(false);

  return (
    <div
      className="relative rounded-2xl md:rounded-3xl -mt-5 mb-10 px-4 py-6 md:px-8 shadow-2xl border border-white/20 backdrop-blur-[14px] bg-gradient-to-br from-[#29196620] via-[#7b2ff25] to-[#36205a15] bg-white/15 flex flex-col md:flex-row items-center gap-8"
      style={{ boxShadow: "0 6px 40px 0 #7b2ff225, 0 1.5px 8px #2e205933" }}
    >
  {/* --- Concept badge top right --- */}
  {conceptName && (
    <span className="absolute top-3 right-4 flex items-center gap-2 px-3 py-1 rounded-full bg-[var(--primary)]/20 border border-[var(--primary)]/40 shadow-sm">
      {conceptLogo && (
        <Image
          src={conceptLogo}
          alt={conceptName}
          width={24}
          height={24}
          className="w-6 h-6 rounded-full object-cover border border-[var(--primary)]/50"
        />
      )}
      <span className="text-sm font-semibold text-[var(--primary)] whitespace-nowrap">
        {conceptName}
      </span>
    </span>
  )}

  {/* --- Logo with modal zoom --- */}
  {logo && (
    <div className="flex-shrink-0 flex flex-col items-center justify-center">
      <button
        type="button"
        aria-label="Zoom page logo"
        onClick={() => setZoomOpen(true)}
        className="
          border border-[var(--primary)] shadow-md
          rounded-2xl p-0 focus:outline-none transition
          hover:scale-105 active:scale-100
          bg-white
        "
        style={{
          boxShadow: "0 2px 24px 0 rgba(123,47,242,0.10)",
        }}
      >
        <Image
          src={logo}
          alt="Page Logo"
          width={120}
          height={120}
          className="object-cover rounded-2xl"
          style={{
            width: 120,
            height: 120,
            minWidth: 64,
            minHeight: 64,
            background: "white",
          }}
          priority
        />
      </button>
      {zoomOpen && (
        <ModalContainer
          title={pageName}
          onClose={() => setZoomOpen(false)}
          className="max-w-2xl"
        >
          <Image
            src={logo}
            alt={pageName}
            className="w-full h-auto max-h-[70vh] rounded-3xl border-4 border-[var(--primary)] object-contain mx-auto shadow-2xl"
            width={400}
            height={400}
          />
        </ModalContainer>
      )}
    </div>
  )}

  {/* --- Title & Settings block --- */}
  <div className="flex-1 flex flex-col min-w-0 items-center md:items-start text-center md:text-left gap-2">
    <h1
      className="
        text-2xl sm:text-3xl md:text-4xl font-extrabold tracking-tight leading-tight
        text-[var(--primary)] drop-shadow-md mb-1 truncate
      "
      style={{
        fontVariationSettings: "'wght' 650, 'wdth' 110",
        lineHeight: 1.1,
        letterSpacing: "-0.02em",
      }}
    >
      {pageName}
    </h1>
    {/* --- SettingsDisplay below title --- */}
    <div className="w-full">
      <SettingsDisplay
        allowCrosslinks={settings.allowCrosslinks}
        ignoreCrosslink={settings.ignoreCrosslink}
        allowCrossworld={settings.allowCrossworld}
        canEdit={settings.canEdit}
        onEdit={settings.onEdit}
        canDelete={canDelete}
        onDelete={onDelete}
        deleteLabel={deleteLabel}
      />
    </div>
  </div>
</div>
  );
}
