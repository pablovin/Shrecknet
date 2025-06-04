"use client";

import { useState, useEffect } from "react";
import { uploadImage } from "@/app/lib/uploadImage";
import { createPage } from "@/app/lib/pagesAPI";
import PageCharacteristicsFields from "./PageCharacteristicsFields";
import { getCharacteristicsForConcept } from "@/app/lib/characteristicsAPI";
import Image from "next/image";


type PageFormValues = {
  name?: string;
  logo?: string;
  allow_crosslinks?: boolean;
  ignore_crosslink?: boolean;
  allow_crossworld?: boolean;
  values?: unknown[];
  [key: string]: unknown;
};

interface PageFormProps {
  selectedWorld: unknown;
  selectedConcept: unknown;
  token: string;
  onSuccess?: (page: unknown) => void;
  initialValues?: PageFormValues;
  onSubmit?: (payload: unknown) => Promise<void>;
  mode?: "create" | "edit";
}

export default function PageForm({
  selectedWorld,
  selectedConcept,
  token,
  onSuccess,
  initialValues = {},
  onSubmit,
  mode = "create"
}: PageFormProps) {
  const [pageName, setPageName] = useState(initialValues.name || "");
  const [pageLogo, setPageLogo] = useState<File | null>(null);
  const [pageLogoUrl, setPageLogoUrl] = useState(initialValues.logo || "");
  const [fields, setFields] = useState<{ [key: string]: unknown }>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [characteristics, setCharacteristics] = useState<unknown[]>([]);

  const [allowCrosslinks, setAllowCrosslinks] = useState(
    initialValues.allow_crosslinks !== undefined ? initialValues.allow_crosslinks : true
  );
  const [ignoreCrosslink, setIgnoreCrosslink] = useState(
    initialValues.ignore_crosslink !== undefined ? initialValues.ignore_crosslink : false
  );
  const [allowCrossworld, setAllowCrossworld] = useState(
    initialValues.allow_crossworld !== undefined ? initialValues.allow_crossworld : true
  );

  // For edit: pre-fill characteristics fields if present
  useEffect(() => {
    if (
      initialValues.values &&
      Array.isArray(initialValues.values) &&
      initialValues.values.length > 0 &&
      characteristics.length > 0
    ) {
      // Map back to { [name]: value } using characteristic name
      const charMap: { [id: number]: unknown } = {};
      initialValues.values.forEach((v: unknown) => {
        charMap[v.characteristic_id] = v.value;
      });
      const newFields: { [key: string]: unknown } = {};
      characteristics.forEach((c) => {
        newFields[c.name] = charMap[c.id] || "";
      });
      setFields(newFields);
    }
  }, [initialValues.values, characteristics]);

  const handleSubmit = async (e: unknown) => {
    e.preventDefault();
    if (!selectedWorld?.id || !selectedConcept?.id || !pageName.trim()) {
      setError("Please fill all required fields.");
      return;
    }

    setError("");
    setLoading(true);
    try {
      let logoUrl = pageLogoUrl;
      if (pageLogo) {
        logoUrl = await uploadImage(pageLogo, "page", pageName, "logo");
      }

      // Map values into the payload format your API expects
      const characteristicPayload = characteristics.map((c) => {
        const rawVal = fields[c.name];
        const list = Array.isArray(rawVal) ? rawVal : rawVal ? [rawVal] : [];
        return {
          characteristic_id: c.id,
          value: list.map((v) => String(v)),
        };
      });

      console.log("Characteristics payload: " + characteristicPayload)
      const payload = {
        gameworld_id: selectedWorld.id,
        concept_id: selectedConcept.id,
        name: pageName,
        logo: logoUrl,
        values: characteristicPayload,
        allow_crosslinks: allowCrosslinks,
        ignore_crosslink: ignoreCrosslink,
        allow_crossworld: allowCrossworld,
      };

      if (onSubmit) {
        await onSubmit(payload); // edit mode: parent handles updatePage etc
      } else {
        const created = await createPage(payload, token);
        if (onSuccess) onSuccess(created);
      }
    } catch (err: unknown) {
      setError("Failed to save page: " + (err?.message || "unknown error"));
    }
    setLoading(false);
  };

  useEffect(() => {
    if (selectedConcept?.id && selectedWorld?.id && token) {
      getCharacteristicsForConcept(selectedConcept.id, token)
        .then(setCharacteristics)
        .catch((err) =>
          console.error("Failed to load characteristics", err)
        );
    }
  }, [selectedConcept, selectedWorld, token]);

  return (
    <form onSubmit={handleSubmit} className="space-y-10 max-w-3xl mx-auto">
      <h2 className="text-2xl font-bold text-[var(--primary)]">
        {mode === "edit" ? "Edit Page Details" : "Final Step: Page Details"}
      </h2>

      {/* PAGE NAME */}
      <div>
        <label className="block mb-1 font-semibold text-sm">
          Page Name <span className="text-red-500">*</span>
        </label>
        <input
          type="text"
          value={pageName}
          onChange={(e) => setPageName(e.target.value)}
          placeholder="Enter a unique name for this page"
          className="w-full px-4 py-2 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--foreground)] focus:outline-none focus:ring focus:border-[var(--primary)]"
          required
        />
      </div>

      {/* PAGE LOGO */}
      <div>
        <label className="block mb-1 font-semibold text-sm">
          Page Logo (optional)
        </label>
        <input
          type="file"
          accept="image/*"
          onChange={(e) => {
            if (e.target.files[0]) {
              setPageLogo(e.target.files[0]);
              setPageLogoUrl(URL.createObjectURL(e.target.files[0]));
            }
          }}
          className="w-full"
        />
        {pageLogoUrl && (
          <Image
            src={pageLogoUrl}
            alt="Preview"
            width={400}        // 24 * 4 (rem → px)
            height={400}       // 24 * 4
            className="mt-3 object-cover rounded-xl border border-[var(--primary)]"
            style={{ width: "6rem", height: "6rem" }} // To ensure width/height in Tailwind
          />
        )}
        <p className="text-xs mt-1 text-[var(--foreground)]/70">
          This image helps distinguish this page in visual lists. Pick unknownthing that matches its theme!
        </p>
      </div>

      {/* PAGE LINKING & DISCOVERY OPTIONS */}
      <div className="border border-[var(--border)] bg-[var(--surface)] rounded-xl p-6 my-8">
        <h3 className="font-semibold text-[var(--primary)] mb-2 text-lg tracking-wide">
          Page Linking & Discovery
        </h3>

        {/* Allow Automatic Crosslinks */}
        <div className="flex items-center gap-4 mb-3">
          <label className="font-medium flex-1 cursor-pointer" htmlFor="allow-crosslinks">
            Allow Automatic Crosslinks
            <div className="text-xs font-normal opacity-80">
              If enabled, this page can receive links from other pages when its name appears in their content.
            </div>
          </label>
          <input
            id="allow-crosslinks"
            type="checkbox"
            className="toggle"
            checked={allowCrosslinks}
            onChange={() => setAllowCrosslinks(v => !v)}
          />
        </div>

        {/* Ignore for Crosslinking */}
        <div className="flex items-center gap-4 mb-3">
          <label className="font-medium flex-1 cursor-pointer" htmlFor="ignore-crosslink">
            Ignore for Crosslinking
            <div className="text-xs font-normal opacity-80">
              If enabled, this page will <span className="font-semibold">never</span> be automatically linked from other pages.
            </div>
          </label>
          <input
            id="ignore-crosslink"
            type="checkbox"
            className="toggle"
            checked={ignoreCrosslink}
            onChange={() => setIgnoreCrosslink(v => !v)}
          />
        </div>

        {/* Allow Cross-World Links */}
        <div className="flex items-center gap-4">
          <label className="font-medium flex-1 cursor-pointer" htmlFor="allow-crossworld">
            Allow Cross-World Links
            <div className="text-xs font-normal opacity-80">
              If enabled, links to this page can come from other worlds (not just the current world).
            </div>
          </label>
          <input
            id="allow-crossworld"
            type="checkbox"
            className="toggle"
            checked={allowCrossworld}
            onChange={() => setAllowCrossworld(v => !v)}
          />
        </div>
      </div>

      {/* CHARACTERISTICS FIELDS */}
      {selectedConcept?.id && (
        <PageCharacteristicsFields
          fields={characteristics}
          values={fields}
          onChange={(key, val) => setFields((prev) => ({ ...prev, [key]: val }))}
          pageID={initialValues?.id ?? null}
          gameworldID={selectedWorld.id}
          pageName={pageName}
        />
      )}

      {error && (
        <div className="text-sm text-red-500 border-l-4 border-red-400 pl-3 py-2 bg-red-50 rounded-md">
          {error}
        </div>
      )}

      <div className="flex justify-end pt-4">
        <button
          type="submit"
          disabled={loading}
          className="px-6 py-2 text-lg rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] hover:bg-[var(--accent)] transition"
        >
          {loading
            ? mode === "edit"
              ? "Saving..."
              : "Creating..."
            : mode === "edit"
            ? "Save Changes"
            : "Create Page"}
        </button>
      </div>
    </form>
  );
}
