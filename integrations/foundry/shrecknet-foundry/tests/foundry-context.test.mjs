import assert from "node:assert/strict";
import test from "node:test";
import { CompanionWorkspace, companionActions } from "../scripts/apps/character-companion.js";
import { documentSnapshot, sheetDocument, openFoundrySource, referenceFromDrop } from "../scripts/apps/foundry-context.js";

function fixture(request = async () => ({})) {
  const docs = new Map();
  const errors = [];
  globalThis.game = { user: { id: "player" }, world: { id: "table-a" } };
  globalThis.foundry = { utils: { fromUuid: async (uuid) => docs.get(uuid) },
    applications: { ux: { TextEditor: { implementation: { getDragEventData: (event) => event.data } } } } };
  const app = { session: {}, connection: { worldId: "city" }, resource: { kind: "companion" },
    client: { companion: request, resolveUrl: (url) => url }, render: async () => {},
    _handleError: async (error) => errors.push(error.message), _form: () => new Map([["query", "Explain this"]]) };
  const w = app.companion = new CompanionWorkspace(app);
  w.selection = { character_agent_id: "speaking-character", availability: "ready", version: 3 };
  w.chat = { id: "chat", character_agent_id: "speaking-character" };
  w.foundryReady = true;
  w.state().includeTable = false;
  return { app, w, docs, errors };
}

function actor(name = "Bao") {
  return { uuid: "Actor.bao", name, documentName: "Actor", _stats: { modifiedTime: 1 },
    testUserPermission: (user, level) => user.id === "player" && level === "OBSERVER",
    system: { gmSecret: "never export" }, flags: { secret: true } };
}

test("unsupported/unrendered systems produce bounded metadata without serializing internal data", async () => {
  const { docs } = fixture();
  docs.set("Actor.bao", actor());
  const snapshot = await documentSnapshot({ uuid: "Actor.bao" });
  assert.equal(snapshot.name, "Bao");
  assert.equal(snapshot.coverage, "partial");
  assert.equal(snapshot.text, "");
  assert.ok(!JSON.stringify(snapshot).includes("never export"));
  docs.get("Actor.bao").testUserPermission = () => false;
  await assert.rejects(documentSnapshot({ uuid: "Actor.bao" }), /unavailable/);
  await assert.rejects(documentSnapshot({ uuid: "javascript:alert(1)" }), /valid Foundry/);
});

test("Ask attachments deduplicate, stay private per chat, and never select a speaking character", async () => {
  const { docs, w } = fixture();
  docs.set("Actor.bao", actor());
  await w.attach("Actor.bao");
  await w.attach("Actor.bao");
  assert.equal(w.state().references.length, 1);
  assert.equal(w.selection.character_agent_id, "speaking-character");
  w.chat = { id: "other" };
  assert.equal(w.state().references.length, 0);
  w.chat = { id: "chat" };
  assert.equal(w.state().references.length, 1);
  w.reset();
  assert.equal(w.state().references.length, 0);
});

test("ambiguous contextual admissions retry the original snapshot and key despite changed game state", async () => {
  const calls = [];
  const { app, w, docs } = fixture(async (path, options) => {
    calls.push(structuredClone({ path, ...options }));
    if (calls.length === 1) throw new Error("connection lost");
    return { turn_id: "turn" };
  });
  w.capabilities = { tools: { web: { available: true } } };
  docs.set("Actor.bao", actor());
  await w.attach("Actor.bao");
  await companionActions.sendCompanionMessage.call(app);
  docs.get("Actor.bao").name = "Changed after admission";
  docs.get("Actor.bao")._stats.modifiedTime = 99;
  w.state().webAllowed = false;
  await companionActions.sendCompanionMessage.call(app);
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(calls[0].body.foundry_context.documents[0].name, "Bao");
  assert.equal(calls[0].body.foundry_context.table, null);
  assert.equal(calls[0].body.tools.web, "allow");
  assert.equal(w.turnId, "turn");
  assert.equal(w.pendingTurn, null);
});

test("web permission does not broaden detached Foundry searches to unrelated world lore", async () => {
  let body;
  const { app, w } = fixture(async (path, options) => { body = options.body; return { turn_id: "turn" }; });
  w.capabilities = { tools: { web: { available: true } } };
  await companionActions.sendCompanionMessage.call(app);
  assert.deepEqual(body.tools, { allowed: ["character_memory", "web"], web: "allow" });
});

test("fresh turns refresh documents and revoked access prevents transmission", async () => {
  const calls = [];
  const { app, w, docs, errors } = fixture(async (path, options) => { calls.push(options); return { turn_id: "turn" }; });
  const doc = actor(); docs.set(doc.uuid, doc);
  await w.attach(doc.uuid);
  doc.name = "Refreshed name";
  await companionActions.sendCompanionMessage.call(app);
  assert.equal(calls[0].body.foundry_context.documents[0].name, "Refreshed name");
  w.busy = false; w.turnId = null;
  doc.testUserPermission = () => false;
  await companionActions.sendCompanionMessage.call(app);
  assert.equal(calls.length, 1);
  assert.match(errors[0], /unavailable/);
});

test("late attachment extraction after sign-out cannot restore private state", async () => {
  const { docs, w } = fixture();
  let resolve;
  foundry.utils.fromUuid = () => new Promise((done) => { resolve = done; });
  const pending = w.attach("Actor.bao");
  w.reset();
  resolve(actor());
  await assert.rejects(pending, /session changed/);
  assert.equal(w.state().references.length, 0);
});

test("source navigation checks table identity and current permissions", async () => {
  const { docs } = fixture();
  let opened = 0;
  const doc = actor(); doc.sheet = { render: async () => { opened++; } };
  docs.set(doc.uuid, doc);
  await assert.rejects(openFoundrySource({ uuid: doc.uuid, foundry_world_id: "table-b" }), /original Foundry table/);
  await openFoundrySource({ uuid: doc.uuid, foundry_world_id: "table-a" });
  assert.equal(opened, 1);
  doc.testUserPermission = () => false;
  await assert.rejects(openFoundrySource({ uuid: doc.uuid, foundry_world_id: "table-a" }), /no longer available/);
});

test("Journal header targets only the current page and drop links are parsed without executing text", () => {
  fixture();
  const page = { documentName: "JournalEntryPage", uuid: "JournalEntry.j.JournalEntryPage.p" };
  const journal = { documentName: "JournalEntry", pages: new Map([["p", page]]) };
  const app = { document: journal, pageId: "p" }; journal.sheet = app;
  assert.equal(sheetDocument(app), page);
  assert.equal(sheetDocument({ document: journal, pageId: "p" }), null);
  assert.equal(referenceFromDrop({ data: { uuid: "Actor.bao" } }), "Actor.bao");
  assert.equal(referenceFromDrop({ data: {}, dataTransfer: { getData: () => "@UUID[Item.sword]{Sword}" } }), "Item.sword");
  assert.equal(referenceFromDrop({ data: { uuid: "javascript:alert(1)" } }), null);
});

test("older backends retain text chat and refuse silent loss of attached documents", async () => {
  let posted = false;
  const { app, w, docs, errors } = fixture(async () => { posted = true; return { turn_id: "t" }; });
  docs.set("Actor.bao", actor());
  await w.attach("Actor.bao");
  w.foundryReady = false;
  await companionActions.sendCompanionMessage.call(app);
  assert.equal(posted, false);
  assert.match(errors[0], /Update the backend/);
  w.state().references = [];
  await companionActions.sendCompanionMessage.call(app);
  assert.equal(posted, true);
});

test("attachments prepared before the first chat follow its creation without changing existing chats", async () => {
  const { app, w, docs } = fixture();
  w.chat = null;
  w.usage = { remaining: 10 };
  docs.set("Actor.bao", actor());
  await w.attach("Actor.bao");
  app.client.companion = async (path, options) => {
    if (options?.method === "POST") return { id: "created", character_agent_id: "speaking-character" };
    if (path === "/selection") return w.selection;
    if (path.startsWith("/candidates")) return { items: [], next_cursor: null };
    if (path.startsWith("/chats?")) return { items: [{ id: "created", character_agent_id: "speaking-character" }], usage: { count: 1, limit: 10, remaining: 9 } };
    if (path === "/chats/created") return { id: "created", character_agent_id: "speaking-character" };
    return { items: [], next_cursor: null };
  };
  await companionActions.createCompanionChat.call(app);
  assert.equal(w.chat.id, "created");
  assert.equal(w.state().references[0].uuid, "Actor.bao");
});

test("definite server rejections allow editing instead of trapping an obsolete selection version", async () => {
  const { app, w } = fixture(async () => { throw Object.assign(new Error("selection conflict"), { status: 409 }); });
  await companionActions.sendCompanionMessage.call(app);
  assert.equal(w.pendingTurn, null);
  assert.equal(w.context().canSend, true);
});
