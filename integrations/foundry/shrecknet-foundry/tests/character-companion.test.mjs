import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { CompanionWorkspace, companionActions } from "../scripts/apps/character-companion.js";
import { ShrecknetClient } from "../scripts/api/shrecknet-client.js";

function fixture(request) {
  const errors = [];
  const app = { session: {}, resource: { kind: "companion" }, render: async () => {},
    history: { navigate() {} }, _handleError: async (e) => errors.push(e.message),
    _form: () => new Map([["query", "Hello"], ["title", "Renamed"]]),
    client: { companion: request, resolveUrl: (url) => url } };
  const w = app.companion = new CompanionWorkspace(app);
  w.selection = { character_agent_id: "hero", version: 4, availability: "ready", character: { name: "Hero", image_url: "javascript:evil()" } };
  w.chat = { id: "chat", character_agent_id: "hero", title: "Story", revision: 2 };
  w.usage = { count: 1, limit: 10, remaining: 9 };
  return { app, w, errors };
}

test("Companion HTTP requests use player auth and UUID admission headers", async () => {
  const client = new ShrecknetClient({ serverUrl: "https://example.test" });
  client.accessToken = "private-token";
  let request;
  client._fetchJson = async (...args) => { request = args; return {}; };
  await client.companion("/chats", { method: "POST", body: { title: "Story" }, idempotencyKey: "stable-key" });
  assert.equal(request[1], "/users/me/character-companion/chats");
  assert.equal(request[2].headers.Authorization, "Bearer private-token");
  assert.equal(request[2].headers["Idempotency-Key"], "stable-key");
  client.accessToken = null;
  await assert.rejects(client.companion("/selection"), (error) => error.status === 401);
});

test("ambiguous admission retries reuse the key and duplicate send clicks are blocked", async () => {
  const keys = [];
  const { app, w } = fixture(async (path, options) => {
    keys.push(options.idempotencyKey);
    if (keys.length === 1) throw new Error("connection lost");
    return { turn_id: "turn" };
  });
  await companionActions.sendCompanionMessage.call(app);
  assert.equal(w.draft, "");
  await companionActions.sendCompanionMessage.call(app);
  await companionActions.sendCompanionMessage.call(app);
  assert.equal(keys.length, 2);
  assert.equal(keys[0], keys[1]);
  assert.equal(w.turnId, "turn");
  assert.equal(w.busy, true);
  assert.equal(w.messages[0].content, "Hello");
});

test("reopening a running conversation recovers the saved reply without another POST", async () => {
  const calls = [];
  let complete = false;
  const { w } = fixture(async (path) => {
    calls.push(path);
    if (path === "/chats/chat") return { id: "chat", character_agent_id: "hero", active_turn_id: complete ? null : "turn" };
    if (path.includes("/messages")) return { items: complete ? [{ role: "assistant", content: "Welcome" }] : [], next_cursor: null };
    if (path === "/turns/turn") { complete = true; return { chat_id: "chat", status: "completed" }; }
    throw new Error(path);
  });
  await w.openChat("chat");
  assert.equal(w.busy, true);
  await w.checkTurn();
  assert.equal(w.busy, false);
  assert.equal(w.messages[0].content, "Welcome");
  assert.equal(calls.filter((p) => p === "/turns/turn").length, 1);
});

test("rename uses revision and deletion needs explicit confirmation", async () => {
  const calls = [];
  const { app, w } = fixture(async (path, options) => { calls.push([path, options]); return { ...w.chat, title: "Renamed", revision: 3 }; });
  await companionActions.confirmDeleteCompanionChat.call(app);
  assert.equal(calls.length, 0);
  await companionActions.renameCompanionChat.call(app);
  assert.deepEqual(calls[0][1].body, { title: "Renamed", expected_revision: 2 });
  assert.equal(w.chat.revision, 3);
});

test("chat quota and availability gate creation while the hero image rejects script URLs", async () => {
  const { app, w, errors } = fixture(async () => { throw new Error("unexpected request"); });
  assert.equal(w.context().imageUrl, null);
  w.usage.remaining = 0;
  await companionActions.createCompanionChat.call(app);
  assert.match(errors[0], /free chat slot/);
  assert.equal(w.context().canCreate, false);
  w.usage.remaining = 1;
  w.selection.availability = "identity_pending";
  assert.equal(w.context().canSend, false);
});

test("pagination prepends chronological history and keeps the server cursor opaque", async () => {
  const { app, w } = fixture(async (path) => {
    assert.equal(path, "/chats/chat/messages?limit=100&before=a%2Fb");
    return { items: [{ content: "Old" }], next_cursor: "next" };
  });
  w.messages = [{ content: "New" }];
  w.before = "a/b";
  await companionActions.olderCompanionMessages.call(app);
  assert.deepEqual(w.messages.map((m) => m.content), ["Old", "New"]);
  assert.equal(w.before, "next");
});

test("sign-out discards late private data and stops reply polling", async () => {
  let resolve;
  const { w } = fixture(() => new Promise((done) => { resolve = done; }));
  const pending = w.openChat("chat");
  w.reset();
  resolve({ id: "chat", character_agent_id: "hero", title: "Private" });
  await assert.rejects(pending, /session changed/);
  assert.equal(w.chat, null);
  assert.deepEqual(w.messages, []);
  assert.equal(w.turnId, null);
});

test("transport failures pause polling; refresh can recover without admitting another turn", async () => {
  const { app, w, errors } = fixture(async () => { throw new Error("offline"); });
  w.busy = true;
  w.turnId = "turn";
  await w.act(() => w.checkTurn());
  assert.deepEqual(errors, ["offline"]);
  assert.equal(w.busy, false);
  assert.equal(w.turnId, "turn");
  assert.equal(w.context().canSend, false);
});

test("the left menu has precisely the requested order and model text stays escaped", async () => {
  const template = await readFile(new URL("../templates/shell.hbs", import.meta.url), "utf8");
  const sidebar = template.split('<aside class="shrecknet-sidebar"')[1].split('</aside>')[0];
  assert.deepEqual([...sidebar.matchAll(/<span>([^<]+)<\/span>/g)].map((m) => m[1]), ["Atlas", "Shreckworlds", "Chronicles", "Companion"]);
  assert.ok(template.includes('{{content}}'));
  assert.ok(!template.includes('{{{content}}}'));
});

test("new chats use the current character, selection version and global context", async () => {
  const calls = [];
  const { app, w } = fixture(async (path, options) => {
    calls.push([path, options]);
    if (options?.method === "POST") return { id: "created", character_agent_id: "hero" };
    if (path === "/selection") return w.selection;
    if (path.startsWith("/candidates")) return { items: [], next_cursor: null };
    if (path.startsWith("/chats?")) return { items: [{ id: "created", character_agent_id: "hero" }], usage: { count: 2, limit: 10, remaining: 8 } };
    if (path === "/chats/created") return { id: "created", character_agent_id: "hero" };
    return { items: [], next_cursor: null };
  });
  await companionActions.createCompanionChat.call(app);
  const admission = calls.find(([, options]) => options?.method === "POST");
  assert.deepEqual(admission[1].body, { character_agent_id: "hero", selection_version: 4, title: "New conversation", default_scope: { surface: "global" } });
  assert.ok(admission[1].idempotencyKey);
  assert.equal(w.chat.id, "created");
});

test("opening another character's tab updates selection with optimistic version", async () => {
  const { w } = fixture(async (path, options) => {
    if (path === "/chats/other") return { id: "other", character_agent_id: "other-hero" };
    if (path === "/selection") {
      assert.deepEqual(options.body, { character_agent_id: "other-hero", expected_version: 4 });
      return { character_agent_id: "other-hero", version: 5, character: { name: "Other Hero" } };
    }
    return { items: [], next_cursor: null };
  });
  await w.openChat("other");
  assert.equal(w.context().name, "Other Hero");
  assert.equal(w.chat.id, "other");
});

test("Stop reply cancels the addressed durable turn without a new admission", async () => {
  const calls = [];
  const { app, w } = fixture(async (path, options) => {
    calls.push([path, options]);
    if (path.endsWith("/cancel")) return { status: "cancelled" };
    if (path === "/turns/turn") return { chat_id: "chat", status: "cancelled" };
    if (path === "/chats/chat") return { id: "chat", character_agent_id: "hero", active_turn_id: null };
    return { items: [], next_cursor: null };
  });
  w.busy = true;
  w.turnId = "turn";
  await companionActions.cancelCompanionTurn.call(app);
  assert.deepEqual(calls.filter(([, options]) => options?.method === "POST").map(([path]) => path), ["/turns/turn/cancel"]);
  assert.equal(w.busy, false);
  assert.equal(w.turnId, null);
});

test("revision failures leave the current chat intact and report the conflict", async () => {
  const { app, w, errors } = fixture(async () => { throw new Error("revision_conflict"); });
  await companionActions.renameCompanionChat.call(app);
  assert.deepEqual(errors, ["revision_conflict"]);
  assert.equal(w.chat.title, "Story");
  assert.equal(w.chat.revision, 2);
});
