import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

globalThis.foundry = { applications: { api: { ApplicationV2: class { async close() {} }, HandlebarsApplicationMixin: base => base } } };
const { ShrecknetConnectionSettings: Settings } = await import("../scripts/apps/shrecknet-connection-settings.js");

function fixture(saved = {}) {
  const store = new Map(Object.entries(saved));
  const writes = [], calls = [];
  globalThis.game = { user: { isGM: true }, settings: {
    get: (_, key) => store.get(key) || "",
    set: async (_, key, value) => { writes.push([key, value]); store.set(key, value); },
  } };
  const app = new Settings();
  app.render = async () => {};
  let fields = new Map();
  app._form = () => fields;
  Object.assign(app.client, {
    validateIntegration: async key => { calls.push(["key", key]); return { default_world_id: null }; },
    listConnectableWorlds: async key => { calls.push(["worlds", key]); return [{ id: "a", name: "A" }, { id: "b", name: "B" }]; },
    checkServiceHealth: async url => { calls.push(["health", url]); return { status: "ok" }; },
  });
  return { app, writes, calls, store, form: values => { fields = new Map(Object.entries(values)); } };
}

async function load(f, extra = {}) {
  f.form({ serverUrl: "https://example.test/backend_api", integrationKey: "setup-only-secret", ...extra });
  await Settings._loadSetupWorlds.call(f.app);
}

test("setup loads Worlds first, tests all services, then explicitly saves", async () => {
  const f = fixture();
  await load(f);
  assert.equal(f.app.setupStage, "world");
  assert.equal(f.app.draft.worldId, "");
  assert.deepEqual(f.writes, []);
  assert.equal(f.app.client.accessToken, null);
  assert.ok(!JSON.stringify(await f.app._prepareContext()).includes("setup-only-secret"));
  f.form({ worldId: "b" });
  await Settings._saveSetup.call(f.app);
  assert.match(f.app.notice, /Test all connections/);
  assert.deepEqual(f.writes, []);
  await Settings._testSetup.call(f.app);
  assert.equal(f.app.checks.length, 4);
  assert.ok(f.app.checks.every(check => check.ok));
  assert.equal((await f.app._prepareContext()).canSave, true);
  assert.deepEqual(f.calls.filter(c => c[0] === "health").map(c => c[1]), [
    "https://example.test/backend_api", "https://example.test/shreckworld-api", "https://example.test/api/shreckrpg",
  ]);
  assert.deepEqual(f.writes, []);
  await Settings._saveSetup.call(f.app);
  assert.equal(f.store.get("worldId"), "b");
  assert.equal(f.store.get("worldName"), "B");
  assert.equal(f.app.integrationKey, null);
  assert.equal((await f.app._prepareContext()).configured, true);
  assert.ok(!JSON.stringify(f.writes).includes("setup-only-secret"));
});

test("admin default, a single legacy World, and missing default are handled explicitly", async () => {
  for (const defaultWorld of ["a", null, "deleted"]) {
    const f = fixture();
    f.app.client.validateIntegration = async () => ({ default_world_id: defaultWorld });
    if (!defaultWorld) f.app.client.listConnectableWorlds = async () => [{ id: "a", name: "A" }];
    await load(f);
    if (defaultWorld === "deleted") {
      assert.equal(f.app.setupStage, "credentials");
      assert.equal(f.app.integrationKey, null);
      assert.match(f.app.notice, /No Worlds/);
    } else {
      assert.equal(f.app.draft.worldId, "a");
      assert.deepEqual(f.app.worldChoices.map(w => w.id), ["a"]);
    }
  }
});

test("revoked keys and Worlds removed after selection prevent saving", async () => {
  for (const revoked of [true, false]) {
    const f = fixture(); await load(f); f.form({ worldId: "a" });
    if (revoked) f.app.client.validateIntegration = async () => { throw new Error("Key revoked"); };
    else f.app.client.listConnectableWorlds = async () => [{ id: "b", name: "B" }];
    await Settings._testSetup.call(f.app);
    assert.equal(f.app.checks[0].ok, false);
    assert.equal(f.app.testedSignature, null);
    assert.equal(f.calls.filter(c => c[0] === "health").length, 3);
    await Settings._saveSetup.call(f.app);
    assert.deepEqual(f.writes, []);
  }
});

test("partial service failures display each result, preserve drafts, and allow retry", async () => {
  const f = fixture(); await load(f); f.form({ worldId: "a", shreckRpgUrl: "https://rpg.example/api" });
  f.app.client.checkServiceHealth = async url => { if (url.includes("rpg.example")) throw new Error("CORS blocked"); };
  await Settings._testSetup.call(f.app);
  assert.equal(f.app.checks.filter(c => c.ok).length, 3);
  assert.match(f.app.checks[3].detail, /CORS blocked/);
  assert.equal(f.app.draft.shreckRpgUrl, "https://rpg.example/api");
  assert.deepEqual(f.writes, []);
  assert.equal((await f.app._prepareContext()).canSave, false);
  f.app.client.checkServiceHealth = async () => ({ status: "ok" });
  await Settings._testSetup.call(f.app);
  assert.equal((await f.app._prepareContext()).canSave, true);
});

test("World or service URL edits invalidate the previous successful test", async () => {
  for (const changes of [{ worldId: "b" }, { shreckWorldUrl: "https://other.example" }]) {
    const f = fixture(); await load(f); f.form({ worldId: "a" });
    await Settings._testSetup.call(f.app);
    f.form(changes);
    await Settings._saveSetup.call(f.app);
    assert.deepEqual(f.writes, []);
    assert.match(f.app.notice, /Test all connections/);
    assert.equal((await f.app._prepareContext()).canSave, false);
  }
});

test("existing setup tests all health URLs without requiring the discarded key", async () => {
  const f = fixture({ serverUrl: "https://example.test/backend_api", worldId: "a", worldName: "A", shreckWorldUrl: "https://library.example/api", shreckRpgUrl: "https://rpg.example/api" });
  f.form({ shreckWorldUrl: "https://new-library.example/api", shreckRpgUrl: "https://rpg.example/api" });
  await Settings._testSetup.call(f.app);
  assert.equal(f.app.checks.length, 3);
  assert.ok(!f.calls.some(c => c[0] === "key" || c[0] === "worlds"));
  await Settings._saveSetup.call(f.app);
  assert.equal(f.store.get("shreckWorldUrl"), "https://new-library.example/api");
  assert.equal(f.store.get("worldId"), "a");
  await Settings._editSetup.call(f.app);
  assert.equal(f.app.editing, true);
  await load(f);
  assert.equal(f.app.draft.shreckWorldUrl, "https://new-library.example/api");
  assert.equal(f.app.draft.shreckRpgUrl, "https://rpg.example/api");
  await Settings._cancelSetup.call(f.app);
  assert.equal(f.app.editing, false);
  assert.equal(f.app.integrationKey, null);
});

test("invalid URLs, empty keys, and unselected Worlds never save", async () => {
  for (const serverUrl of ["javascript:bad", "http://remote.test", "https://user:password@example.test", "https://example.test?key=bad", ""]) {
    const f = fixture(); await load(f, { serverUrl });
    assert.equal(f.calls.length, 0);
    assert.equal(f.app.setupStage, "credentials");
    assert.deepEqual(f.writes, []);
  }
  const f = fixture(); await load(f, { integrationKey: "" });
  assert.match(f.app.notice, /setup key/);
  await load(f); f.form({ worldId: "" }); await Settings._testSetup.call(f.app);
  assert.match(f.app.notice, /Select a Shrecknet World/);
});

test("only a GM can use setup actions and duplicate clicks are ignored", async () => {
  const f = fixture(); globalThis.game.user.isGM = false;
  for (const method of [Settings._loadSetupWorlds, Settings._testSetup, Settings._saveSetup]) await method.call(f.app);
  assert.match(f.app.notice, /Only a Foundry GM/);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.writes, []);
  globalThis.game.user.isGM = true;
  let finish;
  f.app.client.validateIntegration = () => new Promise(resolve => { finish = resolve; });
  f.form({ serverUrl: "https://example.test", integrationKey: "secret" });
  const pending = Settings._loadSetupWorlds.call(f.app);
  await new Promise(resolve => setImmediate(resolve));
  await Settings._loadSetupWorlds.call(f.app);
  finish({ default_world_id: "a" }); await pending;
  assert.equal(f.calls.filter(c => c[0] === "worlds").length, 1);
});

test("closing setup fences pending responses and discards the in-memory key", async () => {
  const f = fixture(); await load(f); f.form({ worldId: "a" });
  // Use a shared promise so all service probes finish together.
  let done; const health = new Promise(resolve => { done = resolve; });
  f.app.client.checkServiceHealth = () => health;
  const pending = Settings._testSetup.call(f.app);
  await new Promise(resolve => setImmediate(resolve));
  await f.app.close(); done({ status: "ok" }); await pending;
  assert.equal(f.app.integrationKey, null);
  assert.equal(f.app.testedSignature, null);
  assert.deepEqual(f.writes, []);
});

test("setup template uses one form, a scrollable body, and a separate action footer", async () => {
  const template = await readFile(new URL("../templates/connection-settings.hbs", import.meta.url), "utf8");
  assert.equal((template.match(/<form\b/g) || []).length, 1);
  assert.ok(template.indexOf('</div>\n    <footer') > 0);
  assert.ok(template.includes('data-action="saveSetup" {{#unless canSave}}disabled'));
  const css = await readFile(new URL("../styles/base.css", import.meta.url), "utf8");
  assert.match(css, /\.shrecknet-setup-body\s*\{[^}]*overflow:\s*auto/);
  assert.match(css, /\.shrecknet-setup-footer\s*\{[^}]*flex:\s*0 0 auto/);
});

test("an HTML or malformed setup response leaves the credential step recoverable", async () => {
  const f = fixture();
  f.app.client.validateIntegration = async () => null;
  await load(f);
  assert.equal(f.app.setupStage, "credentials");
  assert.equal(f.app.integrationKey, null);
  assert.match(f.app.notice, /expected JSON/);
  assert.deepEqual(f.writes, []);
});
