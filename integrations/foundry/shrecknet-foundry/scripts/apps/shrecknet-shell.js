import { ShrecknetClient, ShrecknetApiError } from "../api/shrecknet-client.js";
import { NavigationHistory } from "../navigation/navigation-history.js";
import { getConnectionState, saveConnectionState } from "../settings/connection-settings.js";

const { ApplicationV2, HandlebarsApplicationMixin } = foundry.applications.api;

export class ShrecknetShell extends HandlebarsApplicationMixin(ApplicationV2) {
  static DEFAULT_OPTIONS = {
    id: "shrecknet-shell",
    classes: ["shrecknet", "theme-terminal"],
    tag: "section",
    window: { title: "Shrecknet", icon: "fa-solid fa-terminal", resizable: true },
    position: { width: 840, height: 640 },
    actions: {
      testConnection: ShrecknetShell._testConnection,
      saveConnection: ShrecknetShell._saveConnection,
      signIn: ShrecknetShell._signIn,
      signOut: ShrecknetShell._signOut,
      openHome: ShrecknetShell._openHome,
      openFolder: ShrecknetShell._openFolder,
      openInstance: ShrecknetShell._openInstance,
      openScene: ShrecknetShell._openScene,
      search: ShrecknetShell._search,
      goBack: ShrecknetShell._goBack,
      goForward: ShrecknetShell._goForward,
    },
  };

  static PARTS = { main: { template: "modules/shrecknet-foundry/templates/shell.hbs" } };

  constructor(options = {}) {
    super(options);
    this.connection = getConnectionState();
    this.client = new ShrecknetClient({ serverUrl: this.connection.serverUrl });
    this.session = this.client.loadSession();
    this.worldChoices = [];
    this.notice = null;
    this.resource = { kind: "home", title: this.connection.worldName || "Shrecknet" };
    this.history = new NavigationHistory(this.resource);
  }

  async _prepareContext() {
    return {
      configured: Boolean(this.connection.serverUrl && this.connection.worldId),
      canConfigure: game.user.isGM,
      authenticated: Boolean(this.session?.user),
      user: this.session?.user,
      connection: this.connection,
      worldChoices: this.worldChoices,
      resource: this.resource,
      notice: this.notice,
      canGoBack: this.history.canGoBack,
      canGoForward: this.history.canGoForward,
    };
  }

  _form(event) { return new FormData(event.target.closest("form")); }

  async _loadHome({ push = true } = {}) {
    const world = await this.client.getWorld(this.connection.worldId);
    const ontologies = await Promise.all(world.ontology_ids.map((id) => this.client.getOntology(id)));
    const typeLists = await Promise.all(world.ontology_ids.map((id) => this.client.listEntityTypes(id)));
    const folders = typeLists.flatMap((types, index) => types.map((type) => ({ ...type, ontologyId: world.ontology_ids[index], ontologyName: ontologies[index].name })));
    this.resource = { kind: "home", title: world.name, breadcrumbs: [world.name], folders };
    if (push) this.history.navigate(this.resource);
  }

  async _handleError(error) {
    if (error instanceof ShrecknetApiError && error.isAuthenticationError) {
      this.client.clearSession();
      this.session = null;
      this.notice = "Your Shrecknet session has expired. Sign in again.";
    } else {
      this.notice = error.message || "Unable to complete the Shrecknet request.";
    }
    await this.render();
  }

  static async _testConnection(event) {
    const app = this;
    const data = app._form(event);
    try {
      app.client.setServer(data.get("serverUrl"));
      await app.client.validateIntegration(data.get("integrationKey"));
      app.worldChoices = await app.client.listConnectableWorlds(data.get("integrationKey"));
      app.notice = "Connected to Shrecknet. Select a World and save.";
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  static async _saveConnection(event) {
    const app = this;
    if (!game.user.isGM) return app._handleError(new Error("Only a Foundry GM can configure Shrecknet."));
    const data = app._form(event);
    const worldId = data.get("worldId");
    const selected = app.worldChoices.find((world) => world.id === worldId);
    if (!selected) return app._handleError(new Error("Test the connection and select a Shrecknet World first."));
    try {
      await saveConnectionState({ serverUrl: ShrecknetClient.normalizeServerUrl(data.get("serverUrl")), worldId, worldName: selected.name });
      app.connection = getConnectionState();
      app.client.setServer(app.connection.serverUrl);
      app.notice = "Shrecknet connection saved.";
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  static async _signIn(event) {
    const app = this;
    const data = app._form(event);
    try {
      app.session = { user: await app.client.login(data.get("identifier"), data.get("password")) };
      app.notice = null;
      await app._loadHome({ push: false });
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  static async _signOut() {
    this.client.clearSession();
    this.session = null;
    this.resource = { kind: "home", title: this.connection.worldName };
    this.history = new NavigationHistory(this.resource);
    await this.render();
  }

  static async _openHome() { try { await this._loadHome(); await this.render(); } catch (error) { await this._handleError(error); } }

  static async _openFolder(event) {
    const { ontologyId, definitionId, title } = event.currentTarget.dataset;
    try {
      const page = await this.client.listInstances(ontologyId, definitionId);
      this.resource = { kind: "folder", title, ontologyId, definitionId, breadcrumbs: [this.connection.worldName, title], instances: page.results };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _openInstance(event) {
    try {
      const instance = await this.client.getInstance(event.currentTarget.dataset.instanceId);
      this.resource = { kind: "instance", title: instance.name, instance, breadcrumbs: [this.connection.worldName, instance.name] };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _openScene(event) {
    const { instanceId, sceneId } = event.currentTarget.dataset;
    try {
      const scene = await this.client.getNarrativeScene(instanceId, sceneId);
      this.resource = { kind: "scene", title: scene.name, scene, breadcrumbs: [this.connection.worldName, "Narrative Scenes", scene.name] };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _search(event) {
    const app = this;
    const query = String(app._form(event).get("query") || "").trim();
    if (!query) return;
    try {
      const world = await app.client.getWorld(app.connection.worldId);
      const responses = await Promise.all(world.ontology_ids.map((id) => app.client.search(id, query)));
      const instances = responses.flatMap((result) => [...result.direct_results, ...result.deep_results]).map((result) => result.instance);
      app.resource = { kind: "search", title: `Search: ${query}`, query, breadcrumbs: [app.connection.worldName, "Search"], instances };
      app.history.navigate(app.resource);
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  static async _goBack() { this.resource = this.history.back(); await this.render(); }
  static async _goForward() { this.resource = this.history.forward(); await this.render(); }
}
