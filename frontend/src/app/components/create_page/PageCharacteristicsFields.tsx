"use client";
import React, { useEffect, useState } from "react";
import Image from "next/image";
import { uploadImage } from "@/app/lib/uploadImage";
import { uploadFile } from "@/app/lib/uploadFile";
import { getPages } from "@/app/lib/pagesAPI";
import { useAuth } from "../auth/AuthProvider";
import { getConcept } from "@/app/lib/conceptsAPI";
import PageRefSelectorMD3 from "./PageRefSelectorMD3";

const typeLabels = {
  text: "Normal text",
  string: "Normal text",
  str: "Normal text",
  int: "Only numbers",
  numeric: "Only numbers",
  date: "Choose a date",
  img: "Upload an image",
  image: "Upload an image",
  video: "YouTube video URL",
  audio: "Spotify audio URL",
  link: "External link",
  pdf: "Upload a PDF",
  page_ref: "Select related pages",
  foundry: "Upload Foundry JSON",
};

export default function PageCharacteristicsFields({
  fields,
  values,
  onChange,
  pageID,
  gameworldID,
  pageName: pageNameProp,
}) {
  const { token } = useAuth();
  const [pageOptions, setPageOptions] = useState({});  
  const [newListItem, setNewListItem] = useState({});
  const [conceptNames, setConceptNames] = useState({});
  const [pageName, setPageName] = useState(pageNameProp || "");

  // Fetch or update the page name
  useEffect(() => {
    async function fetchName() {
      if (!pageID) return;
      try {
        const pages = await getPages(token, { id: pageID });
        if (pages?.length) setPageName(pages[0].name);
      } catch {}
    }

    if (pageNameProp !== undefined) {
      setPageName(pageNameProp || "");
    } else if (!pageName && pageID && token) {
      fetchName();
    }
  }, [pageID, pageNameProp, token]);

  useEffect(() => {
    async function loadPageRefs() {
      if (!token || !gameworldID) return;
      const refs = fields.filter((f) => f.type === "page_ref" && f.ref_concept_id);
      const results = {};
      for (const f of refs) {
        try {
          const pages = await getPages(token, {
            gameworld_id: gameworldID,
            concept_id: f.ref_concept_id,
          });
          results[f.id] = pages;
        } catch (e) {
          console.warn(`Could not fetch pages for concept ${f.ref_concept_id}`, e);
        }
      }
      setPageOptions(results);
    }
    loadPageRefs();
  }, [fields, gameworldID, token]);

  // Single or multiple image upload
  const handleUploadImage = async (e, name, isList = false) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;
    if (isList) {
      const urls = [];
      for (let i = 0; i < files.length; i++) {
        const url = await uploadImage(files[i], "pages", pageID, `${name}_${i}`);
        urls.push(url);
      }
      onChange(name, urls);
    } else {
      const url = await uploadImage(files[0], "pages", pageID, name);
      onChange(name, url);
    }
  };

  // Single or multiple PDF upload
  const handleUploadPDF = async (e, name, isList = false) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;
    if (isList) {
      const urls = [];
      for (let i = 0; i < files.length; i++) {
        // For list: /images/pages/page/{pageName}/{pageName}_{i}.pdf
        if (pageName) {
          const url = await uploadFile(
            files[i],
            `pages/page/${pageName}`,
            `${pageName}_${i}.pdf`
          );
          urls.push(url);
        }
      }
      onChange(name, urls);
    } else {
      // Single: /images/pages/page/{pageName}/{pageName}_foundry.pdf
      if (pageName) {
        const url = await uploadFile(
          files[0],
          `pages/page/${pageName}`,
          `${pageName}_foundry.pdf`
        );
        onChange(name, url);
      }
    }
  };

  // Single or multiple Foundry JSON upload
  const handleUploadFoundryJSON = async (e, name, isList = false) => {
    const files = Array.from(e.target.files);
    if (!files.length || !pageName) return;
    if (isList) {
      const urls = [];
      for (let i = 0; i < files.length; i++) {
        const url = await uploadFile(
          files[i],
          `pages/page/${pageName}`,
          `${pageName}_${i}.json`
        );
        urls.push(url);
      }
      onChange(name, urls);
    } else {
      const url = await uploadFile(
        files[0],
        `pages/page/${pageName}`,
        `${pageName}_foundry_sheet.json`
      );
      onChange(name, url);
    }
  };

  useEffect(() => {
    async function loadConceptNames() {
      const needed = fields
        .filter(f => f.type === "page_ref" && f.ref_concept_id)
        .map(f => f.ref_concept_id);
      const uniqueIds = [...new Set(needed)];
      const result = {};
      for (const id of uniqueIds) {
        try {
          const concept = await getConcept(id, token);
          result[id] = concept.name;
        } catch (err) {
          console.warn(`Failed to fetch concept ${id}`, err);
        }
      }
      setConceptNames(result);
    }
    if (token) loadConceptNames();
  }, [fields, token]);

  return (
    <div className="space-y-8">
      {fields.map((field) => {
        const val = values[field.name] || (field.is_list ? [] : "");
        const isMultiple = field.is_list;

        return (
          <div key={field.id} className="flex flex-col gap-2">
            <label className="text-[var(--primary)] font-semibold text-sm flex items-center gap-2">
              {field.logo && (
                <Image src={field.logo} alt="logo" width={50} height={50} className="rounded-full border" />
              )}
              {field.name}
              <span className="ml-2 text-xs text-[var(--foreground)]/60">
                {typeLabels[field.type] || field.type}
                {field.is_list && " (multiple allowed)"}
                {field.type === "page_ref" && field.ref_concept_id && ` – Pages from ${conceptNames[field.ref_concept_id] || `concept ${field.ref_concept_id}`}`}
              </span>
            </label>
            {field.description && (
              <p className="text-xs text-[var(--foreground)]/60 italic">
                {field.description}
              </p>
            )}

            {/* --- Logic for PAGE REFERENCE --- */}
            {field.type === "page_ref" ? (
              <PageRefSelectorMD3
                options={pageOptions[field.id] || []}
                value={val}
                onChange={(newVal) => onChange(field.name, newVal)}
                label={field.name}
                multiple={!!field.is_list}
                required={!!field.required}
                disabled={!!field.readOnly}
              />

            ) : field.type === "foundry" ? (
              // --- Logic for FOUNDRY: JSON only ---
              <div className="flex flex-col gap-2">
                <label className="text-xs text-[var(--primary)] font-semibold mb-1">
                  Foundry Character Sheet (.json){isMultiple ? " (multiple)" : ""}
                </label>
                <input
                  type="file"
                  accept=".json,application/json"
                  multiple={isMultiple}
                  onChange={e => handleUploadFoundryJSON(e, field.name, isMultiple)}
                  disabled={!pageName}
                />
                {/* Show links for each uploaded file */}
                {isMultiple && Array.isArray(val) ? (
                  <ul className="text-xs">
                    {val.map((url, idx) => (
                      <li key={idx}>
                        <a href={url} target="_blank" rel="noopener noreferrer" className="text-[var(--primary)] underline">
                          Download Sheet #{idx + 1}
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : val ? (
                  <a
                    href={val}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-[var(--primary)] underline"
                  >
                    Download uploaded sheet
                  </a>
                ) : null}
              </div>
            ) : field.type === "pdf" ? (
              // --- Logic for PDF only ---
              <div className="flex flex-col gap-2">
                <input
                  type="file"
                  accept="application/pdf"
                  multiple={isMultiple}
                  onChange={(e) => handleUploadPDF(e, field.name, isMultiple)}
                  disabled={!pageName}
                />
                {isMultiple && Array.isArray(val) ? (
                  <ul className="text-xs">
                    {val.map((url, idx) => (
                      <li key={idx}>
                        <a href={url} target="_blank" rel="noopener noreferrer" className="text-[var(--primary)] underline">
                          Download PDF #{idx + 1}
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : val ? (
                  <a
                    href={val}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-[var(--primary)] underline"
                  >
                    Download uploaded PDF
                  </a>
                ) : null}
              </div>
            ) : field.is_list ? (
              // --- GENERIC LIST FIELDS ---
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={newListItem[field.name] || ""}
                    onChange={(e) => setNewListItem({ ...newListItem, [field.name]: e.target.value })}
                    className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm"
                    placeholder="Type a value and click +"
                  />
                  <button
                    type="button"
                    className="bg-[var(--primary)] text-white px-3 py-2 rounded"
                    onClick={() => {
                      const currentList = Array.isArray(val) ? val : [];
                      const newVal = newListItem[field.name];
                      if (newVal && !currentList.includes(newVal)) {
                        onChange(field.name, [...currentList, newVal]);
                        setNewListItem({ ...newListItem, [field.name]: "" });
                      }
                    }}
                  >
                    +
                  </button>
                </div>
                <ul className="text-sm text-[var(--foreground)]/90 space-y-1">
                  {Array.isArray(val) && val.map((v, i) => (
                    <li key={i} className="flex justify-between items-center bg-[var(--surface)] border border-[var(--border)] rounded px-3 py-1">
                      <span>{v}</span>
                      <button
                        type="button"
                        className="text-red-500 hover:text-red-700"
                        onClick={() => onChange(field.name, val.filter((item) => item !== v))}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              // --- NON-LIST FIELDS ---
              (() => {
                switch (field.type) {
                  case "text":
                  case "string":
                  case "str":
                    return (
                      <textarea
                        className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm"
                        value={val}
                        onChange={(e) => onChange(field.name, e.target.value)}
                      />
                    );
                  case "int":
                  case "numeric":
                    return (
                      <input
                        type="number"
                        className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm"
                        value={val}
                        onChange={(e) => onChange(field.name, e.target.value)}
                      />
                    );
                  case "date":
                    return (
                      <input
                        type="date"
                        className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm"
                        value={val}
                        onChange={(e) => onChange(field.name, e.target.value)}
                      />
                    );
                  case "img":
                  case "image":
                    return (
                      <div className="flex flex-col gap-2">
                        <input
                          type="file"
                          accept="image/*"
                          multiple={isMultiple}
                          onChange={(e) => handleUploadImage(e, field.name, isMultiple)}
                        />
                        {isMultiple && Array.isArray(val) ? (
                          <ul className="flex gap-2 flex-wrap">
                            {val.map((url, idx) => (
                              <li key={idx}>
                                <Image
                                  src={url}
                                  alt={`${field.name} ${idx + 1}`}
                                  width={50}
                                  height={50}
                                  className="rounded border border-[var(--primary)]"
                                />
                              </li>
                            ))}
                          </ul>
                        ) : val ? (
                          <Image
                            src={val}
                            alt={field.name}
                            width={50}
                            height={50}
                            className="rounded border border-[var(--primary)]"
                          />
                        ) : null}
                      </div>
                    );
                  case "video":
                  case "audio":
                  case "link":
                    return (
                      <input
                        type="url"
                        className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm"
                        value={val}
                        onChange={(e) => onChange(field.name, e.target.value)}
                      />
                    );
                  default:
                    return <div className="text-sm text-red-400">Unsupported field type</div>;
                }
              })()
            )}
          </div>
        );
      })}
    </div>
  );
}
