import { ShrecknetShell } from "./shrecknet-shell.js";
import { ShrecknetClient } from "../api/shrecknet-client.js";
import { getConnectionState, saveConnectionState, saveServiceUrls, setConnectionStatus } from "../settings/connection-settings.js";

/** GM-only setup: credentials → World → service checks → explicit save.
 * Drafts and the setup key live only in this window; no content session is used.
 */
export class ShrecknetConnectionSettings extends ShrecknetShell {
  static DEFAULT_OPTIONS = {
    ...ShrecknetShell.DEFAULT_OPTIONS,
    id: "shrecknet-connection-settings",
    window: { title: "Shrecknet Connection", icon: "shrecknet-logo-icon", resizable: false },
    position: { width: 560, height: 580 },
    actions: {
      loadSetupWorlds: ShrecknetConnectionSettings._loadSetupWorlds,
      testSetup: ShrecknetConnectionSettings._testSetup,
      saveSetup: ShrecknetConnectionSettings._saveSetup,
      editSetup: ShrecknetConnectionSettings._editSetup,
      cancelSetup: ShrecknetConnectionSettings._cancelSetup,
      setupBack: ShrecknetConnectionSettings._setupBack,
    },
  };

  static PARTS = { main: { template: "modules/shrecknet-foundry/templates/connection-settings.hbs" } };

  constructor(options = {}) {
    super(options);
    // Setup never sends the GM's personal bearer token to any service.
    this.client = new ShrecknetClient({ serverUrl: this.connection.serverUrl });
    this.session = null;
    this.setupGeneration = 0;
    this._resetSetup();
  }

  _resetSetup() {
    this.connection = getConnectionState();
    this.editing = !this.connection.worldId;
    this.setupStage = "credentials";
    this.draft = { serverUrl: this.connection.serverUrl, worldId: this.connection.worldId,
      shreckWorldUrl: this._serviceUrl("shreckWorld"), shreckRpgUrl: this._serviceUrl("shreckRpg") };
    this.worldChoices = [];
    this.integrationKey = null;
    this.defaultWorldId = null;
    this.checks = [];
    this.testedSignature = null;
    this.setupBusy = false;
    this.setupScrollTop = 0;
    this.revealResults = false;
    this.advancedOpen = false;
    this.notice = null;
  }

  _signature(draft = this.draft) {
    return JSON.stringify([draft.serverUrl, draft.worldId, draft.shreckWorldUrl, draft.shreckRpgUrl]);
  }

  async _prepareContext() {
    return { connection: this.connection, draft: this.draft, notice: this.notice,
      showServices: !this.editing || this.setupStage === "world",
      configured: !this.editing, choosingWorld: this.editing && this.setupStage === "world",
      hasSavedConnection: Boolean(this.connection.worldId), busy: this.setupBusy,
      defaultWorldId: this.defaultWorldId, checks: this.checks,
      worldStep: this.setupStage === "world" && !this.setupBusy && !this.checks.length,
      reviewStep: this.setupStage === "world" && (this.setupBusy || this.checks.length > 0),
      canSave: !this.setupBusy && this.testedSignature === this._signature(),
      worldChoices: this.worldChoices.map(world => ({ ...world, selected: String(world.id) === String(this.draft.worldId) })),
    };
  }

  _onRender(context, options) {
    super._onRender(context, options);
    const root = this.element instanceof HTMLElement ? this.element : this.element?.[0];
    const advanced = root?.querySelector(".shrecknet-setup-advanced");
    if (advanced) {
      advanced.open = this.advancedOpen;
      advanced.addEventListener("toggle", () => { if (advanced.isConnected) this.advancedOpen = advanced.open; });
    }
    const body = root?.querySelector(".shrecknet-setup-body");
    if (body) {
      body.scrollTop = this.setupScrollTop || 0;
      body.addEventListener("scroll", () => { this.setupScrollTop = body.scrollTop; });
      if (this.revealResults) {
        const results = root.querySelector("[data-setup-results]");
        body.scrollTop += results.getBoundingClientRect().top - body.getBoundingClientRect().top;
        this.revealResults = false;
      }
    }
    root?.querySelectorAll("input[name], select[name]").forEach(input => input.addEventListener("input", () => {
      if (!(input.name in this.draft)) return;
      this.draft[input.name] = input.value;
      this.testedSignature = null;
      this.checks = [];
      this.notice = null;
      root.querySelector("[data-setup-notice]")?.remove();
      root.querySelectorAll('[data-action="saveSetup"]').forEach(button => { button.disabled = true; });
      const results = root.querySelector("[data-setup-results]");
      if (results) { results.textContent = "Settings changed. Test all connections again before saving."; }
    }));
  }

  _captureDraft(event, target) {
    const data = this._form(event, target);
    for (const name of Object.keys(this.draft)) {
      if (data.has(name)) this.draft[name] = String(data.get(name) || "").trim();
    }
    return data;
  }

  _normalizedDraft() {
    const serverUrl = ShrecknetClient.normalizeServiceUrl(this.draft.serverUrl);
    if (!serverUrl) throw new Error("Enter the Shrecknet API URL.");
    const shreckWorldUrl = ShrecknetClient.normalizeServiceUrl(this.draft.shreckWorldUrl)
      || ShrecknetClient.serviceBaseFromServer(serverUrl, "/shreckworld-api");
    const shreckRpgUrl = ShrecknetClient.normalizeServiceUrl(this.draft.shreckRpgUrl)
      || ShrecknetClient.serviceBaseFromServer(serverUrl, "/api/shreckrpg");
    return { ...this.draft, serverUrl, shreckWorldUrl, shreckRpgUrl };
  }

  async _runSetup(work) {
    if (!game.user.isGM) { this.notice = "Only a Foundry GM can configure Shrecknet."; await this.render(); return; }
    if (this.setupBusy) return;
    const generation = this.setupGeneration;
    const active = () => generation === this.setupGeneration;
    this.setupBusy = true;
    this.notice = null;
    try {
      await this.render();
      await work(active);
    } catch (error) {
      if (active()) this.notice = error.message || "Unable to complete setup. Check the settings and try again.";
    } finally {
      if (active()) { this.setupBusy = false; await this.render(); }
    }
  }

  static async _loadSetupWorlds(event, target) {
    if (!game.user.isGM || this.setupBusy) return this._runSetup(async () => {});
    const data = this._captureDraft(event, target);
    const key = String(data.get("integrationKey") || "").trim();
    return this._runSetup(async active => {
      this.integrationKey = null;
      this.worldChoices = [];
      this.testedSignature = null;
      this.checks = [];
      if (!key) throw new Error("Enter the Foundry setup key from Shrecknet administration.");
      const serverUrl = ShrecknetClient.normalizeServiceUrl(this.draft.serverUrl);
      if (!serverUrl) throw new Error("Enter the Shrecknet API URL.");
      this.client.setServer(serverUrl);
      const validation = await this.client.validateIntegration(key, { signal: AbortSignal.timeout(10000) });
      if (!active()) return;
      const worlds = await this.client.listConnectableWorlds(key, { signal: AbortSignal.timeout(10000) });
      if (!active()) return;
      if (!validation || !Array.isArray(worlds)) throw new Error("The setup endpoints did not return the expected JSON. Check the Shrecknet API URL.");
      this.defaultWorldId = validation.default_world_id || null;
      this.worldChoices = this.defaultWorldId ? worlds.filter(world => String(world.id) === String(this.defaultWorldId)) : worlds;
      if (!this.worldChoices.length) throw new Error("No Worlds are available for this key. Ask a Shrecknet administrator to check its World selection.");
      this.integrationKey = key;
      const selected = this.worldChoices.find(world => String(world.id) === String(this.draft.worldId));
      const sameServer = serverUrl === ShrecknetClient.normalizeServerUrl(this.connection.serverUrl);
      this.draft = { serverUrl, worldId: selected?.id || (this.worldChoices.length === 1 ? this.worldChoices[0].id : ""),
        shreckWorldUrl: (sameServer && this.connection.shreckWorldUrl) || ShrecknetClient.serviceBaseFromServer(serverUrl, "/shreckworld-api"),
        shreckRpgUrl: (sameServer && this.connection.shreckRpgUrl) || ShrecknetClient.serviceBaseFromServer(serverUrl, "/api/shreckrpg") };
      this.setupScrollTop = 0;
      this.setupStage = "world";
    });
  }

  static async _testSetup(event, target) {
    if (!game.user.isGM || this.setupBusy) return this._runSetup(async () => {});
    this._captureDraft(event, target);
    return this._runSetup(async active => {
      this.testedSignature = null;
      this.checks = [];
      const draft = this._normalizedDraft();
      if (!draft.worldId) throw new Error("Select a Shrecknet World before testing.");
      this.draft = draft;
      this.client.setServer(draft.serverUrl);
      const checks = [
        { label: "Shrecknet API", run: () => this.client.checkServiceHealth(draft.serverUrl) },
        { label: "ShreckWorld API", run: () => this.client.checkServiceHealth(draft.shreckWorldUrl) },
        { label: "ShreckRPG API", run: () => this.client.checkServiceHealth(draft.shreckRpgUrl) },
      ];
      if (this.editing) {
        const key = this.integrationKey;
        checks.unshift({ label: "Setup key and selected World", run: async () => {
          if (!key) throw new Error("Go back and enter the setup key again.");
          const validation = await this.client.validateIntegration(key, { signal: AbortSignal.timeout(10000) });
          const worlds = await this.client.listConnectableWorlds(key, { signal: AbortSignal.timeout(10000) });
          if ((validation.default_world_id && String(validation.default_world_id) !== String(draft.worldId))
            || !worlds.some(world => String(world.id) === String(draft.worldId))) {
            throw new Error("This World is no longer available for the key. Go back and load Worlds again.");
          }
        } });
      }
      const results = await Promise.all(checks.map(async check => {
        try { await check.run(); return { label: check.label, ok: true, detail: "Connected" }; }
        catch (error) { return { label: check.label, ok: false, detail: error.message || "Connection failed" }; }
      }));
      if (!active()) return;
      this.checks = results;
      this.revealResults = true;
      if (results.every(result => result.ok)) {
        this.testedSignature = this._signature(draft);
        this.notice = "All connection checks passed. Save when you are ready.";
      } else this.notice = "Some checks failed. Review the results below, correct the URLs if needed, and test again. Nothing has been saved.";
    });
  }

  static async _saveSetup(event, target) {
    if (!game.user.isGM || this.setupBusy) return this._runSetup(async () => {});
    this._captureDraft(event, target);
    return this._runSetup(async active => {
      const draft = this._normalizedDraft();
      if (this.testedSignature !== this._signature(draft)) throw new Error("Test all connections successfully before saving these settings.");
      if (this.editing) {
        const selected = this.worldChoices.find(world => String(world.id) === String(draft.worldId));
        if (!selected) throw new Error("Load Worlds and select a World first.");
        await saveConnectionState({ ...draft, worldName: selected.name });
      } else {
        await saveServiceUrls(draft);
        await setConnectionStatus("online");
      }
      if (!active()) return;
      this.connection = getConnectionState();
      this.editing = false;
      this.integrationKey = null;
      this.notice = "Connection saved. Players can now open Shrecknet and sign in.";
    });
  }

  static async _editSetup() {
    if (!game.user.isGM || this.setupBusy) return;
    this.setupGeneration++;
    this._resetSetup();
    this.editing = true;
    await this.render();
  }

  static async _setupBack() {
    if (!game.user.isGM || this.setupBusy) return;
    this.integrationKey = null;
    this.worldChoices = [];
    this.testedSignature = null;
    this.checks = [];
    this.notice = null;
    this.setupStage = "credentials";
    await this.render();
  }

  static async _cancelSetup() {
    if (!game.user.isGM || this.setupBusy) return;
    this.setupGeneration++;
    this._resetSetup();
    await this.render();
  }

  async close(options = {}) {
    this.setupGeneration++;
    this.integrationKey = null;
    return super.close(options);
  }
}
