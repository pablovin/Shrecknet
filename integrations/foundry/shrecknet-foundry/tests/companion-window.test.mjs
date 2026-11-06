import assert from "node:assert/strict";
import test from "node:test";

class Window {
  async render() { this.rendered = true; return this; }
  async close() { this.rendered = false; }
  setPosition(position) { this.position = position; }
  bringToFront() {}
}
globalThis.foundry = { applications: { api: { ApplicationV2: Window, HandlebarsApplicationMixin: base => base } } };
const store = new Map();
globalThis.localStorage = { getItem: key => store.get(key) || null, setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) };
globalThis.sessionStorage = { getItem: () => null, removeItem() {} };
const settings = { serverUrl: "https://example.test", worldId: "city", worldName: "City" };
globalThis.game = { world: { id: "table" }, user: { id: "player" }, settings: { get: (_, key) => settings[key] || "" } };
const { ShrecknetShell } = await import("../scripts/apps/shrecknet-shell.js");
const { ShrecknetApiError } = await import("../scripts/api/shrecknet-client.js");

test("compact/full modes reuse a controller; close preserves drafts; changed accounts get a fresh window", async () => {
  const app = await ShrecknetShell.openWorkspace({ companion: true });
  app.companion.draft = "Unsent draft";
  app.companion.state().references = [{ uuid: "Actor.bao", name: "Bao" }];
  await app.close();
  const reopened = await ShrecknetShell.openWorkspace();
  assert.equal(reopened, app);
  assert.equal(reopened.companion.draft, "Unsent draft");
  assert.equal(reopened.companion.state().references.length, 1);
  assert.equal(reopened.compact, false);
  store.set("shrecknet-foundry.session.v2", JSON.stringify({ serverUrl: "https://example.test", accessToken: "other-account", user: { id: 2 } }));
  const changed = await ShrecknetShell.openWorkspace();
  assert.notEqual(changed, app);
  assert.equal(changed.companion.draft, "");
  assert.equal(changed.companion.state().references.length, 0);
  await app._handleError(new ShrecknetApiError("old account expired", 401));
  assert.ok(store.has("shrecknet-foundry.session.v2"));
  assert.equal(changed.session.user.id, 2);
});

test("authentication expiry clears all cached private content before another sign-in", async () => {
  const app = new ShrecknetShell();
  app.compact = true;
  for (const key of ["pageCache", "knowledgeSessions", "uploadedChronicleAssets"]) app[key].set("private", "content");
  app.companion.draft = "Private question";
  await app._handleError(new ShrecknetApiError("expired", 401));
  assert.equal(app.session, null);
  assert.equal(app.companion.draft, "");
  for (const key of ["pageCache", "knowledgeSessions", "uploadedChronicleAssets"]) assert.equal(app[key].size, 0);
  assert.equal(app.resource.kind, "companion");
});
