import { safeImageUrl } from "./atlas-content.js";
import { buildSnapshot, documentSnapshot, tableSnapshot, readable, referenceFromDrop, openFoundrySource, contextLimits } from "./foundry-context.js";

const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const id = encodeURIComponent;

// This workspace uses the canonical, owner-only Character Companion contract.
// Poll durable snapshots instead of resubmitting a turn after a disconnect.
export class CompanionWorkspace {
  constructor(app) { this.app = app; this.reset(); }

  reset() {
    clearTimeout(this.timer);
    this.generation = (this.generation || 0) + 1;
    this.chatStates = new Map();
    this.capabilities = null;
    this.ontologies = [];
    this.libraries = [];
    this.foundryReady = false;
    this.tablePreview = null;
    this.contextNotice = "";
    this.pendingTurn = null;
    this.restoreDraftFocus = null;
    this.boundChatId = null;
    Object.assign(this, { selection: null, candidates: [], chats: [], chat: null,
      messages: [], before: null, draft: "", busy: false, locked: false,
      status: "", provisional: "", turnId: null, confirmDelete: false, usage: null, pending: null });
  }

  async request(path, options) {
    const generation = this.generation;
    const session = this.app.session;
    const result = await this.app.client.companion(path, options);
    if (generation !== this.generation || session !== this.app.session) throw new Error("Companion session changed.");
    return result;
  }

  state() {
    const key = this.chat?.id || "unselected";
    if (!this.chatStates.has(key)) this.chatStates.set(key, { references: [], includeTable: true, ontologyId: "", libraryId: "", previews: [], webAllowed: true });
    return this.chatStates.get(key);
  }

  async prepareFoundry() {
    const generation = this.generation;
    const session = this.app.session;
    const worldId = this.app.connection?.worldId;
    if (!worldId || !session) return;
    const results = await Promise.allSettled([
      this.request("/capabilities"), this.app.client.getWorld(worldId),
      this.app.client.listKnowledgeWorlds(this.app._serviceUrl("shreckWorld")),
      tableSnapshot(this.app.connection),
    ]);
    if (generation !== this.generation || session !== this.app.session || worldId !== this.app.connection.worldId) return;
    const [caps, world, libraries, table] = results;
    if (caps.status === "rejected") throw caps.reason;
    this.capabilities = caps.value;
    this.foundryReady = Boolean(caps.value.foundry_context?.versions?.includes(1));
    this.contextNotice = this.foundryReady ? "" : "This server supports text chat only. Update the backend to attach Foundry context.";
    if (world.status === "rejected") throw world.reason;
    const rows = await Promise.all(world.value.ontology_ids.map((id) => this.app.client.getOntology(id)));
    if (generation !== this.generation || session !== this.app.session || worldId !== this.app.connection.worldId) return;
    this.ontologies = rows;
    this.libraries = libraries.status === "fulfilled" ? libraries.value.filter((w) => String(w.shrecknet_world_id) === String(worldId)) : [];
    if (libraries.status === "rejected") this.contextNotice += " Rules libraries could not be loaded; refresh to retry.";
    this.tablePreview = table.status === "fulfilled" ? { ...table.value, shrecknet_world_name: world.value.name } : null;
    if (table.status === "rejected") this.contextNotice += ` ${table.reason.message}`;
  }

  async attach(uuid, app = null) {
    if (this.capabilities && !this.foundryReady) throw new Error("This server supports text chat only. Update the backend to attach documents.");
    if (this.pendingTurn) throw new Error("Resolve the pending message before changing its attachments.");
    const state = this.state();
    const reference = { uuid, app };
    if (state.references.some((ref) => ref.uuid === uuid)) return;
    if (state.references.length >= contextLimits.documents) throw new Error("Attach at most six documents.");
    const generation = this.generation;
    const preview = await documentSnapshot(reference);
    if (generation !== this.generation || state !== this.state()) throw new Error("Companion session changed.");
    if (state.references.some((ref) => ref.uuid === preview.uuid)) return;
    state.references.push({ ...reference, uuid: preview.uuid, name: preview.name });
    state.previews.push(preview);
    this.focusDraft = true;
  }

  context() {
    const state = this.state();
    const character = this.selection?.character;
    return { ...this, app: undefined, timer: undefined,
      contextState: state,
      assignedActor: globalThis.game?.user?.character && readable(game.user.character) ? { uuid: game.user.character.uuid, name: game.user.character.name } : null,
      contextSupported: this.foundryReady,
      tablePreview: this.tablePreview,
      contextNotice: this.contextNotice,
      pendingMessage: Boolean(this.pendingTurn),
      webAvailable: this.capabilities?.tools?.web?.available === true,
      ontologies: this.ontologies.map((o) => ({ ...o, selected: String(o.id) === String(state.ontologyId) })),
      libraries: this.libraries.map((o) => ({ ...o, selected: String(o.id) === state.libraryId })),
      hasSelection: Boolean(this.selection?.character_agent_id),
      name: character?.name || "Your next companion",
      subtitle: character?.subtitle || "Every story is better with someone beside you.",
      imageUrl: safeImageUrl(this.app.client.resolveUrl(character?.image_url)),
      availability: ({ none: "Choose a companion", ready: "Ready to talk", identity_pending: "Preparing character", archived: "Archived", unavailable: "Currently unavailable" })[this.selection?.availability] || "Choose a companion",
      candidates: this.candidates.map((c) => ({ ...c, selected: c.id === this.selection?.character_agent_id })),
      chats: this.chats.map((c) => ({ ...c, selected: c.id === this.chat?.id })),
      messages: this.messages.map((m) => ({ ...m, isUser: m.role === "user",
        indexWarnings: (m.answer_details?.tool_calls || []).filter((call) => call.reason === "incompatible_index" || call.reason === "index_not_ready").map((call) => ({
          ...call, incompatible: call.reason === "incompatible_index",
        })),
        manifest: (m.manifest || []).map((source) => ({ ...source,
        foundrySource: source.source_type === "foundry", canOpen: source.source_type === "foundry" && Boolean(source.reference?.uuid && source.reference?.foundry_world_id),
      })) })),
      canCreate: !this.locked && !this.busy && !this.pendingTurn && this.selection?.availability === "ready" && this.usage?.remaining > 0,
      canSend: !this.locked && !this.busy && !this.turnId && Boolean(this.chat) && this.selection?.availability === "ready",
      locked: this.locked || this.busy || Boolean(this.pendingTurn),
      operationLocked: this.locked,
    };
  }

  async pages(path) {
    const items = [];
    let cursor;
    do {
      const page = await this.request(`${path}${path.includes("?") ? "&" : "?"}limit=100${cursor ? `&cursor=${id(cursor)}` : ""}`);
      items.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    return items;
  }

  async refresh() {
    const [selection, candidates, chats] = await Promise.all([
      this.request("/selection"), this.pages("/candidates"), this.request("/chats?limit=100"),
    ]);
    this.selection = selection;
    this.candidates = candidates;
    this.usage = chats.usage;
    // Preserve legacy accounts above the creation limit as well.
    this.chats = chats.next_cursor ? await this.pages("/chats") : chats.items;
    const next = this.chats.find((c) => c.id === this.chat?.id && c.character_agent_id === selection.character_agent_id)
      || this.chats.find((c) => c.character_agent_id === selection.character_agent_id);
    if (next) await this.openChat(next.id);
    else {
      if (this.chat) this.state().draft = this.draft;
      this.chat = null; this.messages = []; this.before = null; this.busy = false; this.turnId = null;
      this.draft = this.state().draft || "";
      this.pendingTurn = null;
    }
  }

  async openChat(chatId) {
    const previousId = this.chat?.id;
    if (previousId && previousId !== chatId) this.state().draft = this.draft;
    const chat = await this.request(`/chats/${id(chatId)}`);
    if (chat.character_agent_id !== this.selection.character_agent_id) {
      this.selection = await this.request("/selection", { method: "PUT", body: {
        character_agent_id: chat.character_agent_id, expected_version: this.selection.version,
      } });
    }
    const page = await this.request(`/chats/${id(chatId)}/messages?limit=100`);
    const unattached = this.chatStates.get("unselected");
    if (!previousId && unattached?.references.length) {
      this.chatStates.set(chatId, unattached);
      this.chatStates.delete("unselected");
    }
    this.chat = chat;
    this.messages = page.items;
    this.before = page.next_cursor;
    if (previousId !== chatId) {
      this.draft = this.state().draft || "";
      this.provisional = "";
      if (this.pendingTurn) {
        this.pendingTurn = null;
        this.app.notice = "Conversation changed. Check the previous chat for your last message before sending it again.";
      }
    }
    if (chat.active_turn_id) this.pendingTurn = null;
    this.confirmDelete = false;
    this.turnId = chat.active_turn_id;
    this.busy = Boolean(this.turnId);
    this.status = this.busy ? "Checking saved reply…" : "";
  }

  async act(action) {
    if (this.locked) return;
    const generation = this.generation;
    const session = this.app.session;
    this.locked = true;
    clearTimeout(this.timer);
    try {
      await action();
    } catch (error) {
      if (generation === this.generation && session === this.app.session) {
        this.busy = false; // Refresh resumes a saved running turn after a read failure.
        await this.app._handleError(error);
      }
    } finally {
      if (generation === this.generation && session === this.app.session) {
        this.locked = false;
        await this.app.render();
      }
    }
  }

  captureView() {
    const root = this.app.element?.[0] || this.app.element;
    const draft = root?.querySelector?.("[data-companion-draft]");
    if (this.boundChatId === this.chat?.id && draft && globalThis.document?.activeElement === draft) {
      this.restoreDraftFocus = { start: draft.selectionStart, end: draft.selectionEnd };
    }
  }

  bind(root) {
    clearTimeout(this.timer);
    this.boundChatId = this.chat?.id;
    const state = this.state();
    root?.querySelectorAll("details[data-companion-details]").forEach((details) => {
      const key = details.dataset.companionDetails;
      state.details ||= {};
      if (Object.hasOwn(state.details, key)) details.open = state.details[key];
      details.addEventListener("toggle", () => { state.details[key] = details.open; });
    });
    const draft = root?.querySelector("[data-companion-draft]");
    draft?.addEventListener("input", (event) => { this.draft = event.target.value; this.state().draft = this.draft; });
    if (this.focusDraft || this.restoreDraftFocus) {
      draft?.focus();
      if (this.restoreDraftFocus) draft?.setSelectionRange(this.restoreDraftFocus.start, this.restoreDraftFocus.end);
      this.restoreDraftFocus = null;
      this.focusDraft = false;
    }
    const panel = root?.querySelector(".shrecknet-companion");
    panel?.addEventListener("dragover", (event) => event.preventDefault());
    panel?.addEventListener("drop", (event) => {
      event.preventDefault();
      const uuid = referenceFromDrop(event);
      void this.act(async () => {
        if (!uuid) throw new Error("Drop an Actor, Item, or Journal page link.");
        await this.attach(uuid);
      });
    });
    root?.querySelectorAll("[data-companion-scope]").forEach((select) => select.addEventListener("change", () => {
      if (!this.pendingTurn) this.state()[select.dataset.companionScope] = select.value;
    }));
    root?.querySelector("[data-companion-web]")?.addEventListener("change", (event) => {
      if (!this.pendingTurn) this.state().webAllowed = event.currentTarget.checked;
    });
    const log = root?.querySelector(".shrecknet-companion-log");
    if (log) {
      const saved = state.scroll;
      log.scrollTop = saved && !saved.bottom ? saved.top : log.scrollHeight;
      log.addEventListener("scroll", () => { state.scroll = { top: log.scrollTop, bottom: log.scrollHeight - log.scrollTop - log.clientHeight < 30 }; });
    }
    this.keepScroll = false;
    if (this.app.resource.kind === "companion" && this.busy && !this.locked) {
      this.timer = setTimeout(() => void this.act(() => this.checkTurn()), 2000);
    }
  }

  async checkTurn() {
    const turn = await this.request(`/turns/${id(this.turnId)}`);
    if (turn.chat_id !== this.chat.id) throw new Error("The reply belongs to another conversation.");
    this.status = turn.status;
    this.provisional = turn.draft || "";
    if (terminal.has(turn.status)) {
      this.busy = false;
      this.turnId = null;
      this.provisional = "";
      await this.openChat(this.chat.id);
      if (turn.status !== "completed") throw new Error(`Reply ${turn.status}. ${turn.error?.message || "You can send a new message."}`);
    }
  }

  // Keep a UUID and exact body after ambiguous transport failures. Retrying the
  // same action replays admission instead of creating duplicate chats or turns.
  async durable(path, body) {
    const signature = JSON.stringify({ path, body });
    if (this.pending?.signature !== signature) this.pending = { signature, key: crypto.randomUUID() };
    const result = await this.request(path, { method: "POST", body, idempotencyKey: this.pending.key });
    this.pending = null;
    return result;
  }
}

function action(fn, { duringTurn = false } = {}) {
  return function(event, target) {
    const workspace = this.companion;
    if (workspace.busy && !duringTurn) return;
    return workspace.act(() => fn(workspace, event, target));
  };
}

export const companionActions = {
  openCompanion: action(async (w) => {
    await w.refresh();
    await w.prepareFoundry();
    w.app._releaseObjectUrls?.();
    w.app.resource = { kind: "companion", title: "Companion" };
    w.app.history.navigate(w.app.resource);
  }, { duringTurn: true }),
  refreshCompanion: action(async (w) => { await w.refresh(); await w.prepareFoundry(); }, { duringTurn: true }),
  chooseCompanion: action(async (w, event, target) => {
    const characterId = w.app._form(event, target).get("characterId");
    if (!characterId) throw new Error("Choose a companion first.");
    w.selection = await w.request("/selection", { method: "PUT", body: {
      character_agent_id: characterId, expected_version: w.selection.version,
    } });
    w.chat = null;
    await w.refresh();
  }),
  selectCompanionChat: action((w, event, target) => w.openChat((target || event.target.closest("[data-chat-id]")).dataset.chatId)),
  createCompanionChat: action(async (w) => {
    if (w.selection?.availability !== "ready" || !w.usage?.remaining) throw new Error("Choose an available companion and keep a free chat slot.");
    const chat = await w.durable("/chats", { character_agent_id: w.selection.character_agent_id,
      selection_version: w.selection.version, title: "New conversation", default_scope: { surface: "global" } });
    if (!w.chat && w.chatStates.has("unselected")) {
      w.chatStates.set(chat.id, w.chatStates.get("unselected"));
      w.chatStates.delete("unselected");
    }
    w.chat = chat;
    await w.refresh();
  }),
  renameCompanionChat: action(async (w, event, target) => {
    const title = String(w.app._form(event, target).get("title") || "").trim();
    if (!title) throw new Error("Enter a conversation title.");
    w.chat = await w.request(`/chats/${id(w.chat.id)}`, { method: "PATCH", body: { title, expected_revision: w.chat.revision } });
    w.chats = w.chats.map((c) => c.id === w.chat.id ? w.chat : c);
  }),
  deleteCompanionChat: action(async (w) => { w.confirmDelete = true; }),
  dismissDeleteCompanionChat: action(async (w) => { w.confirmDelete = false; }),
  confirmDeleteCompanionChat: action(async (w) => {
    if (!w.confirmDelete || !w.chat) return;
    await w.request(`/chats/${id(w.chat.id)}`, { method: "DELETE" });
    w.chatStates.delete(w.chat.id);
    w.chat = null;
    w.confirmDelete = false;
    await w.refresh();
  }),
  olderCompanionMessages: action(async (w) => {
    if (!w.before) return;
    const page = await w.request(`/chats/${id(w.chat.id)}/messages?limit=100&before=${id(w.before)}`);
    w.messages = [...page.items, ...w.messages];
    w.before = page.next_cursor;
    w.keepScroll = true;
  }),
  toggleCompanionTable: action(async (w) => {
    if (w.pendingTurn) throw new Error("Resolve the pending message first.");
    w.state().includeTable = !w.state().includeTable;
  }),
  removeCompanionContext: action(async (w, event, target) => {
    if (w.pendingTurn) throw new Error("Resolve the pending message first.");
    const uuid = target.dataset.uuid;
    w.state().references = w.state().references.filter((r) => r.uuid !== uuid);
    w.state().previews = w.state().previews.filter((r) => r.uuid !== uuid);
  }),
  attachCompanionActor: action(async (w) => {
    if (game.user.character) await w.attach(game.user.character.uuid);
  }),
  attachCompanionLink: action(async (w, event, target) => {
    const link = String(w.app._form(event, target).get("documentLink") || "").trim();
    const uuid = link.match(/^@UUID\[([^\]]+)\]/)?.[1] || link;
    await w.attach(uuid);
  }),
  companionSuggestion: action(async (w, event, target) => {
    if (w.pendingTurn) throw new Error("Resolve the pending message first.");
    w.draft = target.dataset.question; w.state().draft = w.draft; w.focusDraft = true;
  }),
  openCompanionSource: action(async (w, event, target) => {
    const source = w.messages.flatMap((m) => m.manifest || []).find((s) => s.evidence_id === target.dataset.evidenceId);
    if (!source || source.source_type !== "foundry") throw new Error("This source is unavailable.");
    const uuid = target.dataset.uuid;
    if (uuid && !(source.structured_evidence?.links || []).some((link) => link.uuid === uuid)) throw new Error("This link was not attached to the message.");
    await openFoundrySource(uuid ? { ...source.reference, uuid } : source.reference);
  }, { duringTurn: true }),
  sendCompanionMessage: action(async (w, event, target) => {
    const text = String(w.app._form(event, target).get("query") || "").trim();
    if ((!text && !w.pendingTurn) || !w.chat) return;
    const path = `/chats/${id(w.chat.id)}/turns`;
    if (w.pendingTurn && w.pendingTurn.path !== path) throw new Error("Resolve the pending message in its original chat.");
    if (!w.pendingTurn) {
      const state = w.state();
      if (state.references.length && !w.foundryReady) throw new Error("Update the backend before sending document context.");
      const web = state.webAllowed && w.capabilities?.tools?.web?.available === true ? "allow" : "disabled";
      const body = { text, selection_version: w.selection.version, context: { surface: "global" }, tools: { web } };
      if (w.foundryReady && (state.includeTable || state.references.length)) {
        const generation = w.generation;
        const snapshot = await buildSnapshot(w.app.connection, state);
        if (generation !== w.generation) throw new Error("Companion session changed.");
        body.foundry_context = snapshot;
        if (state.includeTable && state.ontologyId) body.context.ontology_id = Number(state.ontologyId);
        if (state.includeTable && state.libraryId) body.context.shreckworld_id = state.libraryId;
        // Do not search unrelated World lore when table context is removed.
      } else if (w.foundryReady) body.tools.allowed = ["character_memory", ...(web === "allow" ? ["web"] : [])];
      w.pendingTurn = { path, body, key: crypto.randomUUID() };
    }
    const pending = w.pendingTurn;
    let turn;
    try {
      turn = await w.request(pending.path, { method: "POST", body: pending.body, idempotencyKey: pending.key });
    } catch (error) {
      // A definite rejection permits editing; transport/5xx failures keep exact bytes.
      if (error.status >= 400 && error.status < 500 && error.status !== 408) w.pendingTurn = null;
      throw error;
    }
    w.pendingTurn = null;
    w.turnId = turn.turn_id;
    w.busy = true;
    w.status = "Thinking…";
    const snapshot = pending.body.foundry_context;
    if (snapshot) { w.state().previews = snapshot.documents; if (snapshot.table) w.tablePreview = snapshot.table; }
    const manifest = snapshot ? [
      ...(snapshot.table ? [{ title: `Foundry snapshot · ${snapshot.table.title}`, excerpt: snapshot.table.description }] : []),
      ...snapshot.documents.map((doc) => ({ title: `Foundry snapshot · ${doc.name}`, excerpt: doc.text })),
    ] : [];
    if (!w.messages.some((message) => message.turn_id === turn.turn_id)) w.messages.push({ role: "user", content: pending.body.text, manifest });
    w.draft = "";
    w.state().draft = "";
  }),
  cancelCompanionTurn: action(async (w) => {
    if (w.turnId) await w.request(`/turns/${id(w.turnId)}/cancel`, { method: "POST" });
    await w.checkTurn();
  }, { duringTurn: true }),
};
