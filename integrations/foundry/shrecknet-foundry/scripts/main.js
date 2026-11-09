import { readable, sheetDocument } from "./apps/foundry-context.js";
import { ShrecknetShell } from "./apps/shrecknet-shell.js";
import { ShrecknetConnectionSettings } from "./apps/shrecknet-connection-settings.js";
import { registerConnectionSettings } from "./settings/connection-settings.js";
import { startConnectionMonitor } from "./settings/connection-monitor.js";

Hooks.once("ready", () => startConnectionMonitor());

Hooks.once("ready", () => {
  game.socket.on("module.shrecknet-foundry", (payload) => {
    if (payload?.type !== "showImage" || !payload.imageUrl) return;
    const ImagePopout = globalThis.ImagePopout || foundry.applications?.apps?.ImagePopout;
    if (ImagePopout) new ImagePopout(payload.imageUrl, { title: payload.title || "Shrecknet" }).render(true);
  });
});

Hooks.once("init", () => {
  registerConnectionSettings(ShrecknetConnectionSettings);
  console.info("[Shrecknet] Foundry module initialized");
});

Hooks.on("getSceneControlButtons", (controls) => {
  controls.shrecknet ??= {
    name: "shrecknet",
    title: "Shrecknet Worlds",
    icon: "shrecknet-logo-icon",
    order: 900,
    tools: {},
  };
  controls.shrecknet.tools.open = {
    name: "open",
    title: "Shrecknet Worlds",
    icon: "shrecknet-logo-icon",
    order: 1,
    button: true,
    visible: true,
    onChange: () => void ShrecknetShell.openWorkspace().catch((error) => ui.notifications.error(error.message)),
  };
});

// One window/controller is reused by the full workspace and compact Companion.
function askCompanion(doc = null, app = null) {
  return ShrecknetShell.openWorkspace({ companion: true, document: doc, sourceApp: app })
    .catch((error) => ui.notifications.error(error.message));
}

Hooks.on("getSceneControlButtons", (controls) => {
  if (!controls.shrecknet) return;
  controls.shrecknet.tools.companion = {
    name: "companion", title: "Ask Companion", icon: "fa-solid fa-comment-dots",
    order: 2, button: true, visible: true, onChange: () => void askCompanion(),
  };
});

// Render a direct title-bar control rather than burying it in the header menu.
Hooks.on("renderApplicationV2", (app, element) => {
  const root = element?.[0] || element || app.element;
  const header = root?.querySelector?.(".window-header");
  if (!header) return;
  const existing = header.querySelector(".shrecknet-ask-companion");
  if (!readable(sheetDocument(app))) { existing?.remove(); return; }
  if (existing) return;
  const button = header.ownerDocument.createElement("button");
  button.type = "button";
  button.className = "header-control icon fa-solid fa-comment-dots shrecknet-ask-companion";
  button.title = "Ask Companion";
  button.setAttribute("aria-label", "Ask Companion");
  // Stop ApplicationV2's delegated click handling; this control owns its callback.
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const doc = sheetDocument(app);
    if (!readable(doc)) { ui.notifications.error("This document is not available to you."); return; }
    void askCompanion(doc, app);
  });
  header.insertBefore(button, header.querySelector('[data-action="toggleControls"], [data-action="minimize"], [data-action="close"]'));
});

for (const kind of ["Actor", "Item", "JournalEntryPage"]) {
  Hooks.on(`get${kind}ContextOptions`, (app, entries) => {
    const resolve = (target) => {
      const element = target?.[0] || target;
      const uuid = element?.closest?.("[data-uuid]")?.dataset.uuid;
      if (uuid) return foundry.utils.fromUuid(uuid);
      const row = element?.closest?.("[data-entry-id],[data-document-id],[data-page-id]");
      const id = row?.dataset.entryId || row?.dataset.documentId || row?.dataset.pageId;
      return app.collection?.get(id) || app.document?.pages?.get(id);
    };
    entries.push({ label: "Ask Companion", icon: "fa-solid fa-comment-dots",
      onClick: async (_event, target) => {
        try {
          const doc = await resolve(target);
          if (!readable(doc)) throw new Error("This document is not available to you.");
          await askCompanion(doc);
        } catch (error) { ui.notifications.error(error.message); }
      } });
  });
}
