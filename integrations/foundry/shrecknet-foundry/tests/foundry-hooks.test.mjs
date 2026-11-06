import assert from "node:assert/strict";
import test from "node:test";

const hooks = new Map();
globalThis.Hooks = { once() {}, on(name, fn) { hooks.set(name, [...(hooks.get(name) || []), fn]); } };
globalThis.foundry = { applications: { api: { ApplicationV2: class {}, HandlebarsApplicationMixin: base => base } }, utils: {} };
globalThis.game = { user: { id: "player" } };
const errors = [];
globalThis.ui = { notifications: { error: message => errors.push(message) } };
const { ShrecknetShell } = await import("../scripts/apps/shrecknet-shell.js");
await import("../scripts/main.js");
const launches = [];
ShrecknetShell.openWorkspace = async (options) => { launches.push(options || {}); };
const doc = { uuid: "Actor.bao", documentName: "Actor", testUserPermission: (user, level) => user.id === "player" && level === "OBSERVER" };

test("v14 header action attaches the active document once without submitting", async () => {
  const hook = hooks.get("getHeaderControlsApplicationV2")[0];
  const app = { document: doc }; doc.sheet = app;
  const controls = [];
  hook(app, controls); hook(app, controls);
  assert.equal(controls.length, 1);
  assert.equal(controls[0].action, "shrecknetCompanion");
  await controls[0].onClick({}, {});
  assert.deepEqual(launches.at(-1), { companion: true, document: doc, sourceApp: app });
  const administrative = [];
  hook({ document: doc }, administrative);
  assert.equal(administrative.length, 0);
  const unavailable = [];
  hook({ document: { ...doc, testUserPermission: () => false } }, unavailable);
  assert.equal(unavailable.length, 0);
});

test("v14 context menu callback uses the second target argument and rechecks document permissions", async () => {
  const app = { collection: new Map([["bao", doc]]) };
  const entries = [];
  hooks.get("getActorContextOptions")[0](app, entries);
  const target = { closest: selector => selector.includes("data-entry-id") ? { dataset: { entryId: "bao" } } : null };
  await entries[0].onClick({ type: "pointerup" }, target);
  assert.equal(launches.at(-1).document, doc);
  app.collection.clear();
  await entries[0].onClick({ type: "pointerup" }, target);
  assert.match(errors.at(-1), /not available/);
});

test("workspace and Companion scene controls use the same window entry point", async () => {
  const controls = {};
  for (const hook of hooks.get("getSceneControlButtons")) hook(controls);
  assert.deepEqual(Object.keys(controls.shrecknet.tools), ["open", "companion"]);
  controls.shrecknet.tools.open.onChange();
  controls.shrecknet.tools.companion.onChange();
  assert.deepEqual(launches.at(-2), {});
  assert.equal(launches.at(-1).companion, true);
});
