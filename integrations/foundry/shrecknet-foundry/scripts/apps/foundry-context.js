// Read-only, system-independent context. Never serialize system data or flags.
export const contextLimits = { documents: 6, bytes: 65536, text: 8000, description: 4000 };
const kinds = new Set(["Actor", "Item", "JournalEntryPage"]);
const uuidPattern = /^(?:[A-Za-z0-9_-]+\.)+[A-Za-z0-9_-]+$/;
const excluded = "script,style,template,iframe,canvas,button,nav,[hidden],[inert],[aria-hidden='true'],input[type='hidden'],input[type='password'],.secret:not(.revealed),.gm-only,[data-visibility='gm'],.editor-menu,.prosemirror,.prosemirror-menubar";

export function readable(doc) {
  return Boolean(doc && kinds.has(doc.documentName) && doc.testUserPermission?.(game.user, "OBSERVER"));
}

export function sheetDocument(app) {
  const doc = app?.document || app?.object;
  // Only actual document sheets participate; never attach administrative dialogs
  // that happen to carry an Actor/Item document reference.
  if (!doc || doc.sheet !== app) return null;
  if (doc.documentName === "JournalEntry") return doc.pages?.get(app.pageId);
  return kinds.has(doc?.documentName) ? doc : null;
}

function visible(element) {
  for (let node = element; node; node = node.parentElement) {
    if (node.matches?.(excluded)) return false;
    const style = globalThis.getComputedStyle?.(node);
    if (style?.display === "none" || style?.visibility === "hidden" || style?.visibility === "collapse" || style?.opacity === "0" || style?.contentVisibility === "hidden") return false;
  }
  return true;
}

export function cleanContent(root, { live = false } = {}) {
  const copy = root.cloneNode(true);
  const readonlyFields = [];
  if (live) {
    for (const input of root.querySelectorAll("input[readonly],input[disabled]")) {
      if (!visible(input) || !["text", "number", "checkbox"].includes(input.type)) continue;
      const label = input.closest("label") || [...root.querySelectorAll("label[for]")].find((label) => label.htmlFor === input.id);
      if (!label || !visible(label)) continue;
      const name = label.textContent.trim();
      const value = input.type === "checkbox" ? input.checked : input.value;
      if (name && name.length <= 80 && String(value).length <= 500) readonlyFields.push({ label: name, value });
    }
  }
  // Match clones to live nodes before removal so computed visibility is respected.
  if (live) {
    const originals = [root, ...root.querySelectorAll("*")];
    const clones = [copy, ...copy.querySelectorAll("*")];
    originals.forEach((node, index) => { if (!visible(node)) clones[index].remove(); });
    if (!visible(root)) return { text: "", fields: [], links: [] };
  }
  copy.querySelectorAll(excluded).forEach((node) => node.remove());
  copy.querySelectorAll("[style]").forEach((node) => {
    if (node.style.display === "none" || ["hidden", "collapse"].includes(node.style.visibility) || node.style.opacity === "0" || node.style.contentVisibility === "hidden") node.remove();
  });
  if (live) copy.querySelectorAll("details:not([open])").forEach((details) => {
    const summary = details.querySelector(":scope > summary");
    details.replaceChildren(...(summary ? [summary] : []));
  });
  // Never include editable, unsaved or password/hidden form values in prose.
  copy.querySelectorAll("input,select,textarea,[contenteditable='true']").forEach((node) => node.remove());
  copy.querySelectorAll("br,p,div,section,article,h1,h2,h3,h4,li,dt,dd,tr").forEach((node) => node.append("\n"));
  const text = (copy.textContent || "").replace(/[ \t]+/g, " ").replace(/\n\s*\n/g, "\n").trim();
  // Keep labels only when a sheet exposes an explicit, readable definition pair.
  const fields = [...copy.querySelectorAll("dt")].flatMap((node) => {
    const value = node.nextElementSibling;
    if (value?.tagName !== "DD") return [];
    return [{ label: node.textContent.trim(), value: value.textContent.trim() }];
  }).filter((field) => field.label && field.label.length <= 80 && field.value.length <= 500);
  const uuidLinks = [...text.matchAll(/@UUID\[([^\]]+)\](?:\{([^}]+)\})?/g)].map((match) => ({ uuid: match[1], label: match[2] || "Linked document" }));
  const links = [...copy.querySelectorAll("[data-uuid]")].map((node) => ({ uuid: node.dataset.uuid, label: node.textContent.trim() })).concat(uuidLinks)
    .filter((link) => uuidPattern.test(link.uuid) && link.label && link.label.length <= 255);
  return { text, fields: [...fields, ...readonlyFields].slice(0, 100), links: links.slice(0, 20) };
}

function htmlContent(html) {
  // Template contents stay inert: never render descriptions or run enrichers,
  // macros, document embeds, inline rolls or HTML event handlers to extract text.
  const template = document.createElement("template");
  template.innerHTML = String(html || "");
  return cleanContent(template.content);
}

export async function tableSnapshot(connection) {
  const gameWorld = game.world;
  const { text } = await htmlContent(gameWorld?.description || "");
  if (text.length > contextLimits.description) throw new Error("The table description exceeds 4,000 characters. Remove table context or shorten its description.");
  return { foundry_world_id: gameWorld.id, title: gameWorld.title || gameWorld.id,
    description: text || null, foundry_version: String(game.version || ""),
    system_id: game.system.id, system_title: game.system.title || game.system.id,
    system_version: String(game.system.version || ""),
    shrecknet_world_id: connection.worldId || null, shrecknet_world_name: connection.worldName || null };
}

export async function documentSnapshot(reference) {
  if (!uuidPattern.test(reference.uuid || "") || reference.uuid.length > 512) throw new Error("Attach a valid Foundry document UUID.");
  let doc = await foundry.utils.fromUuid(reference.uuid);
  if (doc?.documentName === "JournalEntry") {
    const current = doc.pages?.get(doc.sheet?.pageId);
    const pages = [...(doc.pages?.contents || [])].filter(readable);
    doc = readable(current) ? current : (pages.length === 1 ? pages[0] : null);
    if (!doc) throw new Error("Open the Journal page you want to attach, then choose Ask Companion.");
  }
  if (!readable(doc)) throw new Error(`Remove the unavailable attachment: ${reference.name || reference.uuid}.`);
  let content = { text: "", fields: [], links: [] };
  if (doc.documentName === "JournalEntryPage" && doc.type === "text") {
    content = await htmlContent(doc.text?.content);
  } else {
    const app = reference.app?.rendered && sheetDocument(reference.app)?.uuid === doc.uuid ? reference.app : doc.sheet;
    const element = app?.rendered ? (app.element?.[0] || app.element) : null;
    const root = element?.querySelector(".window-content");
    if (root) content = cleanContent(root, { live: true });
  }
  if (content.text.length > contextLimits.text) throw new Error(`“${doc.name}” exceeds 8,000 readable characters. Attach a smaller page or remove it.`);
  const links = [];
  for (const link of content.links) {
    const target = await foundry.utils.fromUuid(link.uuid);
    if (readable(target) && !links.some((row) => row.uuid === link.uuid)) links.push(link);
  }
  // Recheck after asynchronous enrich/resolution, without caching revoked content.
  if (!readable(doc)) throw new Error("Document access changed. Remove the attachment and try again.");
  return { foundry_world_id: game.world.id, uuid: doc.uuid, kind: doc.documentName, name: doc.name,
    captured_at: new Date().toISOString(), source_revision: doc._stats?.modifiedTime == null ? null : String(doc._stats.modifiedTime),
    parent_uuid: doc.parent?.uuid || null, coverage: "partial", text: content.text,
    fields: content.fields, links };
}

export async function buildSnapshot(connection, state) {
  if (state.references.length > contextLimits.documents) throw new Error("Attach at most six documents.");
  const snapshot = { version: 1, captured_at: new Date().toISOString(),
    table: state.includeTable ? await tableSnapshot(connection) : null,
    documents: await Promise.all(state.references.map(documentSnapshot)) };
  if (new TextEncoder().encode(JSON.stringify(snapshot)).length > contextLimits.bytes) throw new Error("Attached context exceeds 64 KiB. Remove an attachment before sending.");
  return snapshot;
}

export function referenceFromDrop(event) {
  const data = foundry.applications.ux.TextEditor.implementation.getDragEventData(event);
  if (uuidPattern.test(data.uuid || "")) return data.uuid;
  const link = event.dataTransfer?.getData("text/plain")?.match(/^@UUID\[([^\]]+)\](?:\{[^}]*\})?$/);
  return link && uuidPattern.test(link[1]) ? link[1] : null;
}

export async function openFoundrySource(reference) {
  if (!reference.foundry_world_id || reference.foundry_world_id !== game.world.id) throw new Error("Open this source in its original Foundry table.");
  const doc = await foundry.utils.fromUuid(reference.uuid);
  if (!readable(doc)) throw new Error("This source is no longer available to you.");
  if (doc.documentName === "JournalEntryPage") return doc.parent.sheet.render({ force: true, pageId: doc.id });
  return doc.sheet.render({ force: true });
}
