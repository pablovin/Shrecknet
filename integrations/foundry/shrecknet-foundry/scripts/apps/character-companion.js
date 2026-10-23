import { safeImageUrl } from "./atlas-content.js";

const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const id = encodeURIComponent;

// This workspace uses the canonical, owner-only Character Companion contract.
// Poll durable snapshots instead of resubmitting a turn after a disconnect.
export class CompanionWorkspace {
  constructor(app) { this.app = app; this.reset(); }

  reset() {
    clearTimeout(this.timer);
    this.generation = (this.generation || 0) + 1;
    Object.assign(this, { selection: null, candidates: [], chats: [], chat: null,
      messages: [], before: null, draft: "", busy: false, locked: false,
      status: "", turnId: null, confirmDelete: false, usage: null, pending: null });
  }

  async request(path, options) {
    const generation = this.generation;
    const session = this.app.session;
    const result = await this.app.client.companion(path, options);
    if (generation !== this.generation || session !== this.app.session) throw new Error("Companion session changed.");
    return result;
  }

  context() {
    const character = this.selection?.character;
    return { ...this, app: undefined, timer: undefined,
      name: character?.name || "Your next companion",
      subtitle: character?.subtitle || "Every story is better with someone beside you.",
      imageUrl: safeImageUrl(this.app.client.resolveUrl(character?.image_url)),
      availability: this.selection?.availability || "Choose a companion",
      candidates: this.candidates.map((c) => ({ ...c, selected: c.id === this.selection?.character_agent_id })),
      chats: this.chats.map((c) => ({ ...c, selected: c.id === this.chat?.id })),
      messages: this.messages.map((m) => ({ ...m, isUser: m.role === "user" })),
      canCreate: !this.locked && !this.busy && this.selection?.availability === "ready" && this.usage?.remaining > 0,
      canSend: !this.locked && !this.busy && !this.turnId && Boolean(this.chat) && this.selection?.availability === "ready",
      locked: this.locked || this.busy,
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
    const next = this.chats.find((c) => c.id === this.chat?.id)
      || this.chats.find((c) => c.character_agent_id === selection.character_agent_id);
    if (next) await this.openChat(next.id);
    else { this.chat = null; this.messages = []; this.before = null; this.busy = false; this.turnId = null; }
  }

  async openChat(chatId) {
    const chat = await this.request(`/chats/${id(chatId)}`);
    if (chat.character_agent_id !== this.selection.character_agent_id) {
      this.selection = await this.request("/selection", { method: "PUT", body: {
        character_agent_id: chat.character_agent_id, expected_version: this.selection.version,
      } });
    }
    const page = await this.request(`/chats/${id(chatId)}/messages?limit=100`);
    this.chat = chat;
    this.messages = page.items;
    this.before = page.next_cursor;
    this.draft = "";
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

  bind(root) {
    clearTimeout(this.timer);
    root?.querySelector("[data-companion-draft]")?.addEventListener("input", (event) => { this.draft = event.target.value; });
    const log = root?.querySelector(".shrecknet-companion-log");
    if (log && !this.keepScroll) log.scrollTop = log.scrollHeight;
    this.keepScroll = false;
    if (this.app.resource.kind === "companion" && this.busy && !this.locked) {
      this.timer = setTimeout(() => void this.act(() => this.checkTurn()), 2000);
    }
  }

  async checkTurn() {
    const turn = await this.request(`/turns/${id(this.turnId)}`);
    if (turn.chat_id !== this.chat.id) throw new Error("The reply belongs to another conversation.");
    this.status = turn.status;
    if (terminal.has(turn.status)) {
      this.busy = false;
      this.turnId = null;
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
    w.app.resource = { kind: "companion", title: "Companion" };
    w.app.history.navigate(w.app.resource);
  }, { duringTurn: true }),
  refreshCompanion: action((w) => w.refresh()),
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
  sendCompanionMessage: action(async (w, event, target) => {
    const text = String(w.app._form(event, target).get("query") || "").trim();
    if (!text || !w.chat) return;
    const turn = await w.durable(`/chats/${id(w.chat.id)}/turns`, {
      text, selection_version: w.selection.version, context: { surface: "global" },
    });
    w.turnId = turn.turn_id;
    w.busy = true;
    w.status = "Thinking…";
    w.messages.push({ role: "user", content: text });
    w.draft = "";
  }),
  cancelCompanionTurn: action(async (w) => {
    if (w.turnId) await w.request(`/turns/${id(w.turnId)}/cancel`, { method: "POST" });
    await w.checkTurn();
  }, { duringTurn: true }),
};
