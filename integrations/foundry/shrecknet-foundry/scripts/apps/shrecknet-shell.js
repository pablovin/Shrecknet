import { ShrecknetClient, ShrecknetApiError } from "../api/shrecknet-client.js";
import { NavigationHistory } from "../navigation/navigation-history.js";
import { getConnectionState, saveConnectionState } from "../settings/connection-settings.js";
import { checkConfiguredConnection } from "../settings/connection-monitor.js";

const { ApplicationV2, HandlebarsApplicationMixin } = foundry.applications.api;

function getActionTarget(event, target) {
  return target || event.target.closest("[data-action]");
}

function nonBlank(value) {
  const text = String(value || "").trim();
  return text || null;
}

function recordName(page) {
  return nonBlank(page.name) || nonBlank(page.entities?.[0]?.alias) || "Untitled record";
}

export class ShrecknetShell extends HandlebarsApplicationMixin(ApplicationV2) {
  static DEFAULT_OPTIONS = {
    id: "shrecknet-shell",
    classes: ["shrecknet", "theme-terminal"],
    tag: "section",
    window: { title: "Shrecknet", icon: "fa-solid fa-folder-tree", resizable: true },
    position: { width: 980, height: 700 },
    actions: {
      testConnection: ShrecknetShell._testConnection,
      saveConnection: ShrecknetShell._saveConnection,
      checkConnection: ShrecknetShell._checkConnection,
      signIn: ShrecknetShell._signIn,
      signOut: ShrecknetShell._signOut,
      openHome: ShrecknetShell._openHome,
      openFolder: ShrecknetShell._openFolder,
      openSceneCollection: ShrecknetShell._openSceneCollection,
      openInstance: ShrecknetShell._openInstance,
      openScene: ShrecknetShell._openScene,
      search: ShrecknetShell._search,
      searchScenes: ShrecknetShell._searchScenes,
      selectTextSource: ShrecknetShell._selectTextSource,
      showOnChat: ShrecknetShell._showOnChat,
      showImageToPlayers: ShrecknetShell._showImageToPlayers,
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
    this.connectionTested = false;
    this.notice = null;
    this.isLoading = false;
    this.folders = [];
    this.activeTextSourceByEntity = new Map();
    this.resource = { kind: "home", title: this.connection.worldName || "Shrecknet" };
    this.pageCache = new Map();
    this.relationshipCardsByInstance = new Map();
    this.propertyNamesByEntityDefinition = new Map();
    this.history = new NavigationHistory(this.resource);
  }

  async _prepareContext() {
    if (this.session?.user && this.connection.worldId && this.resource.kind === "home" && !this.folders.length) {
      try {
        await this._loadHome({ push: false });
      } catch (error) {
        this.notice = error.message || "Unable to load the Shrecknet World index.";
      }
    }
    return {
      configured: Boolean(this.connection.serverUrl && this.connection.worldId),
      canConfigure: game.user.isGM,
      authenticated: Boolean(this.session?.user),
      user: this.session?.user,
      connection: this.connection,
      worldChoices: this.worldChoices,
      hasWorldChoices: this.worldChoices.length > 0,
      connectionTested: this.connectionTested,
      resource: await this._presentResource(),
      folders: this.folders,
      notice: this.notice,
      isLoading: this.isLoading,
      canGoBack: this.history.canGoBack,
      canGoForward: this.history.canGoForward,
      canShowPlayers: game.user.isGM,
    };
  }

  _homeBreadcrumb(link = false) {
    return { label: this.connection.worldName, action: link ? "openHome" : null };
  }

  _folderBreadcrumb(folder, link = false) {
    return {
      label: folder.title,
      action: link ? "openFolder" : null,
      ontologyId: folder.ontologyId,
      definitionId: folder.definitionId,
    };
  }

  _formatPropertyValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  }

  async _loadPropertyNames(instance) {
    const definitionIds = [...new Set(instance.entities.map((entity) => entity.definition_id).filter(Boolean))];
    await Promise.all(definitionIds.map(async (definitionId) => {
      const cacheKey = `${instance.ontology_id}:${definitionId}`;
      if (this.propertyNamesByEntityDefinition.has(cacheKey)) return;
      try {
        const properties = await this.client.listEntityProperties(instance.ontology_id, definitionId);
        this.propertyNamesByEntityDefinition.set(
          cacheKey,
          new Map(properties.map((property) => [property.id, property.name])),
        );
      } catch (_) {
        this.propertyNamesByEntityDefinition.set(cacheKey, new Map());
      }
    }));
  }

  async _presentEntity(entity, relationshipCards, ontologyId) {
    const text = nonBlank(entity.text);
    const generatedText = nonBlank(entity.autogenerated_text);
    const hasBothTextSources = Boolean(text && generatedText);
    const activeTextSource = hasBothTextSources ? this.activeTextSourceByEntity.get(entity.entity_instance_id) || "text" : text ? "text" : generatedText ? "generated" : null;
    return {
      ...entity,
      avatarUrl: this.client.resolveUrl(entity.node_avatar_url),
      textHtml: text ? await this._enrichHtml(text) : null,
      generatedTextHtml: generatedText ? await this._enrichHtml(generatedText) : null,
      hasText: Boolean(text), hasGeneratedText: Boolean(generatedText), hasBothTextSources,
      showText: activeTextSource === "text", showGeneratedText: activeTextSource === "generated",
      relationships: (entity.relationships || []).map((relationship) => ({
        ...relationship, card: relationshipCards.get(relationship.target_entity_id),
      })),
      properties: (entity.properties || []).map((property) => ({
        ...property,
        displayName: this.propertyNamesByEntityDefinition
          .get(`${ontologyId}:${entity.definition_id}`)?.get(property.definition_id)
          || `Property ${property.definition_id}`,
        displayValue: this._formatPropertyValue(property.value),
      })),
    };
  }

  _presentPageCard(page) {
    const image = page.entities?.find((entity) => entity.node_avatar_url)?.node_avatar_url;
    return {
      ...page,
      displayName: recordName(page),
      imageUrl: this.client.resolveUrl(image),
    };
  }

  async _resolveRelationshipCards(instance) {
    const cached = this.relationshipCardsByInstance.get(instance.instance_id);
    if (cached) return cached;
    const targetIds = [...new Set(instance.entities.flatMap((entity) =>
      (entity.relationships || []).map((relationship) => relationship.target_entity_id),
    ).filter(Boolean))];
    if (!targetIds.length) {
      const empty = new Map();
      this.relationshipCardsByInstance.set(instance.instance_id, empty);
      return empty;
    }
    const resolved = await this.client.resolveEntities(instance.ontology_id, targetIds);
    const cards = new Map(resolved.results.map((record) => [record.entity_instance_id, {
      instanceId: record.instance_id,
      name: record.entity_alias || record.instance_name || "Untitled record",
      imageUrl: this.client.resolveUrl(record.avatar_url),
    }]));
    this.relationshipCardsByInstance.set(instance.instance_id, cards);
    return cards;
  }

  async _enrichHtml(html) {
    const editor = globalThis.TextEditor || foundry.applications?.ux?.TextEditor?.implementation;
    return editor?.enrichHTML ? editor.enrichHTML(html, { async: true }) : html;
  }

  async _presentResource() {
    if (this.resource.kind !== "instance") return this.resource;
    const instance = this.resource.instance;
    const relationshipCards = await this._resolveRelationshipCards(instance);
    await this._loadPropertyNames(instance);
    const entities = await Promise.all(instance.entities.map((entity) =>
      this._presentEntity(entity, relationshipCards, instance.ontology_id),
    ));
    const related_content = instance.related_content?.map((item) => ({
      ...item, displayName: item.name || "Untitled record", imageUrl: this.client.resolveUrl(item.image),
    })) || [];
    const pageCard = this._presentPageCard(instance);
    return { ...this.resource, instance: { ...instance, displayName: pageCard.displayName, pageImageUrl: pageCard.imageUrl, entities, related_content } };
  }

  async _withLoading(work) {
    this.isLoading = true;
    await this.render();
    try {
      return await work();
    } finally {
      this.isLoading = false;
    }
  }

  _form(event, target) {
    const form = getActionTarget(event, target).closest("form");
    if (!form) throw new Error("The Shrecknet form is unavailable.");
    return new FormData(form);
  }

  async _loadHome({ push = true } = {}) {
    const world = await this.client.getWorld(this.connection.worldId);
    const ontologies = await Promise.all(world.ontology_ids.map((id) => this.client.getOntology(id)));
    const typeLists = await Promise.all(world.ontology_ids.map((id) => this.client.listEntityTypes(id)));
    this.folders = typeLists.flatMap((types, index) => types.map((type) => ({
      ...type,
      ontologyId: world.ontology_ids[index],
      ontologyName: ontologies[index].name,
      imageUrl: this.client.resolveUrl(type.image_url || ontologies[index].image_url),
    })));
    this.resource = { kind: "home", title: world.name, breadcrumbs: [this._homeBreadcrumb()], folders: this.folders, statusText: this.folders.length + " directories · WORLD INDEX LOADED" };
    if (push) this.history.navigate(this.resource);
  }

  async _loadSceneCollection({ query = "", push = true } = {}) {
    const world = await this.client.getWorld(this.connection.worldId);
    const summaries = (await Promise.all(world.ontology_ids.map((id) => this.client.listSceneSummaries(id, { query })))).flat();
    const groups = [...summaries.reduce((byPage, scene) => {
      const group = byPage.get(scene.instance_id) || {
        instanceId: scene.instance_id, name: scene.source_page_name,
        imageUrl: this.client.resolveUrl(scene.source_page_image), scenes: [],
      };
      group.scenes.push(scene);
      byPage.set(scene.instance_id, group);
      return byPage;
    }, new Map()).values()];
    this.resource = {
      kind: "sceneCollection", title: "Narrative Scenes", query,
      breadcrumbs: [this._homeBreadcrumb(true), { label: "Narrative Scenes" }], groups,
    };
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
      app.connectionTested = true;
      app.connection = { ...app.connection, serverUrl: app.client.serverUrl };
      app.notice = app.worldChoices.length
        ? "Connection verified. Select a Shrecknet World and enable the connection."
        : "Connection verified, but this service key cannot access any Shrecknet Worlds. Ask a Shrecknet administrator to add a World to the integration key.";
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
      app.client.setServer(data.get("serverUrl"));
      await app.client.ping();
      await saveConnectionState({ serverUrl: app.client.serverUrl, worldId, worldName: selected.name });
      app.connection = getConnectionState();
      app.client.setServer(app.connection.serverUrl);
      app.notice = "Shrecknet connection saved.";
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  static async _checkConnection() {
    const app = this;
    if (!game.user.isGM) return app._handleError(new Error("Only a Foundry GM can test Shrecknet."));
    try {
      await checkConfiguredConnection();
      app.connection = getConnectionState();
      app.notice = "Shrecknet is online.";
      await app.render();
    } catch (error) {
      app.connection = getConnectionState();
      await app._handleError(error);
    }
  }

  static async _signIn(event) {
    const app = this;
    const data = app._form(event);
    try {
      app.session = { user: await app._withLoading(() => app.client.login(data.get("identifier"), data.get("password"))) };
      app.notice = null;
      await app._withLoading(() => app._loadHome({ push: false }));
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  static async _signOut() {
    this.client.clearSession();
    this.session = null;
    this.folders = [];
    this.resource = { kind: "home", title: this.connection.worldName };
    this.history = new NavigationHistory(this.resource);
    await this.render();
  }

  static async _openHome() { try { await this._withLoading(() => this._loadHome()); await this.render(); } catch (error) { await this._handleError(error); } }

  static async _openSceneCollection() {
    try { await this._withLoading(() => this._loadSceneCollection()); await this.render(); }
    catch (error) { await this._handleError(error); }
  }

  static async _openFolder(event, target) {
    const { ontologyId, definitionId, title } = getActionTarget(event, target).dataset;
    if (!ontologyId || !definitionId) {
      return this._handleError(new Error("The selected Shrecknet folder is missing its identifiers."));
    }
    try {
      const pages = await this._withLoading(() => this.client.listInstancePages(ontologyId, definitionId));
      pages.forEach((page) => this.pageCache.set(page.instance_id, page));
      const folderContext = { title, ontologyId, definitionId };
      this.resource = { kind: "folder", title, ontologyId, definitionId, folderContext, breadcrumbs: [this._homeBreadcrumb(true), this._folderBreadcrumb(folderContext)], pages: pages.map((page) => this._presentPageCard(page)) };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _openInstance(event, target) {
    const instanceId = getActionTarget(event, target).dataset.instanceId;
    if (!instanceId) return this._handleError(new Error("The selected Shrecknet record is missing its identifier."));
    try {
      const instance = await this._withLoading(async () => {
        const page = this.pageCache.get(instanceId) || await this.client.getInstance(instanceId);
        await this._resolveRelationshipCards(page);
        await this._loadPropertyNames(page);
        return page;
      });
      const folderContext = this.resource.kind === "folder"
        ? this.resource.folderContext
        : this.resource.folderContext || null;
      this.resource = {
        kind: "instance",
        title: instance.name,
        instance,
        folderContext,
        breadcrumbs: [
          this._homeBreadcrumb(true),
          ...(folderContext ? [this._folderBreadcrumb(folderContext, true)] : []),
          { label: recordName(instance) },
        ],
      };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _openScene(event, target) {
    const { instanceId, sceneId } = getActionTarget(event, target).dataset;
    if (!instanceId || !sceneId) {
      return this._handleError(new Error("The selected Narrative Scene is missing its identifiers."));
    }
    try {
      const { scene, sourcePage } = await this._withLoading(async () => {
        const loadedScene = await this.client.getNarrativeScene(instanceId, sceneId);
        const entityIds = [
          ...loadedScene.relates_to.map((item) => item.entity_instance_id),
          ...loadedScene.milestones.flatMap((milestone) => milestone.relates_to.map((item) => item.entity_instance_id)),
        ];
        if (entityIds.length) {
          const resolved = await this.client.resolveEntities(loadedScene.ontology_id, [...new Set(entityIds)]);
          const byEntityId = new Map(resolved.results.map((item) => [item.entity_instance_id, item]));
          const decorate = (relation) => ({ ...relation, record: byEntityId.get(relation.entity_instance_id) });
          loadedScene.relates_to = loadedScene.relates_to.map(decorate);
          loadedScene.milestones = loadedScene.milestones.map((milestone) => ({ ...milestone, relates_to: milestone.relates_to.map(decorate) }));
        }
        const page = this.pageCache.get(instanceId) || await this.client.getInstance(instanceId);
        this.pageCache.set(instanceId, page);
        return { scene: loadedScene, sourcePage: page };
      });
      this.resource = { kind: "scene", title: scene.name, scene, sourcePage, breadcrumbs: [this._homeBreadcrumb(true), { label: "Narrative Scenes", action: "openSceneCollection" }, { label: scene.name }] };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _search(event, target) {
    const app = this;
    const query = String(app._form(event, target).get("query") || "").trim();
    if (!query) return;
    try {
      const pages = await app._withLoading(async () => {
        const world = await app.client.getWorld(app.connection.worldId);
        const pagesByOntology = await Promise.all(world.ontology_ids.map((id) => app.client.searchPages(id, query)));
        return [...new Map(pagesByOntology.flat().map((page) => [page.instance_id, page])).values()];
      });
      pages.forEach((page) => app.pageCache.set(page.instance_id, page));
      app.resource = { kind: "search", title: `Search: ${query}`, query, breadcrumbs: [app._homeBreadcrumb(true), { label: "Search" }], instances: pages.map((page) => app._presentPageCard(page)) };
      app.history.navigate(app.resource);
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  static async _searchScenes(event, target) {
    const query = String(this._form(event, target).get("sceneQuery") || "").trim();
    try { await this._withLoading(() => this._loadSceneCollection({ query })); await this.render(); }
    catch (error) { await this._handleError(error); }
  }

  static async _selectTextSource(event, target) {
    const { entityId, source } = getActionTarget(event, target).dataset;
    if (!entityId || !["text", "generated"].includes(source)) return;
    this.activeTextSourceByEntity.set(entityId, source);
    await this.render();
  }

  static async _showOnChat(event, target) {
    const app = this;
    const entityId = getActionTarget(event, target).dataset.entityId;
    const entity = app.resource.instance?.entities.find((item) => item.entity_instance_id === entityId);
    if (!entity) return app._handleError(new Error("The selected document section is unavailable."));
    const text = nonBlank(entity.text);
    const generatedText = nonBlank(entity.autogenerated_text);
    const source = text && generatedText ? app.activeTextSourceByEntity.get(entityId) || "text" : text ? "text" : "generated";
    const html = source === "text" ? await app._enrichHtml(text) : await app._enrichHtml(generatedText);
    const imageUrl = app.client.resolveUrl(entity.node_avatar_url)
      || app._presentPageCard(app.resource.instance).imageUrl;
    const escape = foundry.utils?.escapeHTML || ((value) => String(value));
    const title = escape(app.resource.instance.name || app.resource.title);
    const section = escape(entity.alias || "Record");
    await ChatMessage.create({
      content: `<article class="shrecknet-chat-record"><h2>${title}</h2><p>${section} · ${source === "text" ? "Text" : "Generated text"}</p>${imageUrl ? `<img src="${escape(imageUrl)}" alt="" style="max-width: 100%;">` : ""}<div>${html || ""}</div></article>`,
      speaker: ChatMessage.getSpeaker(),
    });
  }

  static async _showImageToPlayers(event, target) {
    if (!game.user.isGM) return this._handleError(new Error("Only a Foundry GM can show an image to all players."));
    const { imageUrl, title } = getActionTarget(event, target).dataset;
    if (!imageUrl) return this._handleError(new Error("This Shrecknet image is unavailable."));
    game.socket.emit("module.shrecknet-foundry", { type: "showImage", imageUrl, title: title || "Shrecknet" });
  }

  static async _goBack() { this.resource = this.history.back(); await this.render(); }
  static async _goForward() { this.resource = this.history.forward(); await this.render(); }
}
