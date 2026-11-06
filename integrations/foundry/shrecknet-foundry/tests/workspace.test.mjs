import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

globalThis.foundry = { applications: { api: { ApplicationV2: class {}, HandlebarsApplicationMixin: (base) => base } } };
const { ShrecknetShell } = await import("../scripts/apps/shrecknet-shell.js");

function fixture(worlds = []) {
  const calls = [], errors = [];
  const app = Object.create(ShrecknetShell.prototype);
  Object.assign(app, { connection: { worldId: "atlas-a", worldName: "The Atlas", serverUrl: "https://example.test/backend_api" },
    session: { user: { id: 1 } }, resource: { kind: "home" },
    history: { navigate(resource) { calls.push(resource); } },
    _releaseObjectUrls() {}, render: async () => {}, _withLoading: work => work(),
    _handleError: async error => errors.push(error.message),
    client: {
      listKnowledgeWorlds: async () => worlds,
      listKnowledgeSources: async (base, id) => { calls.push(id); return [{ id: `book-${id}`, title: id }]; },
    },
  });
  return { app, calls, errors };
}

test("bookshelf opens only linked libraries directly, including multiple libraries", async () => {
  const { app, calls } = fixture([
    { id: "one", name: "One", shrecknet_world_id: "atlas-a" },
    { id: "other", shrecknet_world_id: "atlas-b" },
    { id: "unlinked", shrecknet_world_id: null },
    { id: "two", name: "Two", shrecknet_world_id: "atlas-a" },
  ]);
  await ShrecknetShell._openKnowledgeWorlds.call(app);
  assert.deepEqual(calls.slice(0, 2), ["one", "two"]);
  assert.deepEqual(app.resource.libraries.map(library => library.id), ["one", "two"]);
  assert.match(app.resource.libraries[0].sources[0].readerUrl, /sourceId=book-one/);
  assert.equal(app.resource.kind, "knowledgeWorlds");
});

test("an unlinked World has an empty bookshelf without falling back to other Worlds", async () => {
  const { app, calls } = fixture([{ id: "other", shrecknet_world_id: "atlas-b" }]);
  await ShrecknetShell._openKnowledgeWorlds.call(app);
  assert.deepEqual(app.resource.libraries, []);
  assert.equal(calls.length, 1); // Navigation only; no source calls.
});

test("bookshelf sign-out and World changes discard late private results", async () => {
  for (const change of [app => { app.session = null; }, app => { app.connection.worldId = "atlas-b"; }]) {
    const { app } = fixture();
    let finish;
    app.client.listKnowledgeWorlds = () => new Promise(resolve => { finish = resolve; });
    const pending = ShrecknetShell._openKnowledgeWorlds.call(app);
    change(app);
    finish([{ id: "one", shrecknet_world_id: "atlas-a" }]);
    await pending;
    assert.equal(app.resource.kind, "home");
  }
});

test("bookshelf service errors preserve the current view and remain retryable", async () => {
  const { app, errors } = fixture();
  app.client.listKnowledgeWorlds = async () => { throw new Error("Service offline"); };
  await ShrecknetShell._openKnowledgeWorlds.call(app);
  assert.deepEqual(errors, ["Service offline"]);
  assert.equal(app.resource.kind, "home");
});

test("Chronicles list does not publish results after sign-out", async () => {
  const { app } = fixture();
  let finish;
  app.client.listChronicles = () => new Promise(resolve => { finish = resolve; });
  const pending = ShrecknetShell._openChronicles.call(app);
  app.session = null;
  finish([{ id: 3, title: "Private" }]);
  await pending;
  assert.equal(app.resource.kind, "home");
});

test("four-menu template exposes the bookshelf, personal Chronicles and Companion sources", async () => {
  const template = await readFile(new URL("../templates/shell.hbs", import.meta.url), "utf8");
  assert.ok(!template.includes('data-action="openElder"'));
  assert.ok(!template.includes('data-action="queryKnowledge"'));
  assert.ok(template.includes("{{#each resource.libraries}}"));
  assert.ok(template.includes("Change companion"));
  assert.ok(template.includes("{{#each manifest}}"));
  assert.ok(template.includes("Your private notes and stories shared with you."));
});

test("protected image fetches cannot recreate URLs after their session or view was cleared", async () => {
  const { app } = fixture();
  app.resourceObjectUrlsByKey = new Map();
  app.resourceObjectUrls = [];
  let finish;
  const pending = app._loadObjectUrl('cover', () => new Promise(resolve => { finish = resolve; }));
  app.resourceObjectUrls = [];
  finish(new Blob(['private']));
  await assert.rejects(pending, /content changed/);
  assert.equal(app.resourceObjectUrlsByKey.size, 0);
});

