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

function windowFixture() {
  const children = [];
  const native = { native: true };
  const header = {
    ownerDocument: { createElement() { return { attributes: {}, listeners: {}, setAttribute(k, v) { this.attributes[k] = v; }, addEventListener(k, v) { this.listeners[k] = v; }, remove() { children.splice(children.indexOf(this), 1); } }; } },
    querySelector(selector) { return selector === ".shrecknet-ask-companion" ? children[0] : native; },
    insertBefore(button, before) { assert.equal(before, native); children.push(button); },
  };
  return { root: { querySelector: () => header }, children };
}

test("v14 title-bar button attaches the active document once without submitting", async () => {
  const hook = hooks.get("renderApplicationV2")[0];
  const app = { document: doc }; doc.sheet = app;
  const { root, children } = windowFixture();
  hook(app, root); hook(app, root);
  assert.equal(children.length, 1);
  assert.equal(children[0].attributes["aria-label"], "Ask Companion");
  let stopped = false;
  children[0].listeners.click({ preventDefault() {}, stopPropagation() { stopped = true; } });
  await Promise.resolve();
  assert.equal(stopped, true);
  assert.deepEqual(launches.at(-1), { companion: true, document: doc, sourceApp: app });
  const administrative = windowFixture();
  hook({ document: doc }, administrative.root);
  assert.equal(administrative.children.length, 0);
  const count = launches.length;
  const permission = doc.testUserPermission;
  doc.testUserPermission = () => false;
  children[0].listeners.click({ preventDefault() {}, stopPropagation() {} });
  assert.equal(launches.length, count);
  assert.match(errors.at(-1), /not available/);
  hook(app, root);
  assert.equal(children.length, 0);
  doc.testUserPermission = permission;
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
