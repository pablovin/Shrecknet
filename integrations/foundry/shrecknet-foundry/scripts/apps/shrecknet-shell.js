import { knowledgeReaderUrl, presentKnowledgeTurn, formatKnowledgeTurn } from "./knowledge-companion.js";
import { presentElderHistory, presentElderSources, submitElderTurn } from "./elder-conversation.js";
import { selectedTextSource, groupRelationships, renderAtlasChat, safeImageUrl } from "./atlas-content.js";
import { ShrecknetClient, ShrecknetApiError } from "../api/shrecknet-client.js";
import { NavigationHistory } from "../navigation/navigation-history.js";
import { getConnectionState, saveConnectionState, saveServiceUrls } from "../settings/connection-settings.js";
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
    position: { width: 1080, height: 760 },
    actions: {
      testConnection: ShrecknetShell._testConnection,
      saveConnection: ShrecknetShell._saveConnection,
      saveServiceUrls: ShrecknetShell._saveServiceUrls,
      checkConnection: ShrecknetShell._checkConnection,
      signIn: ShrecknetShell._signIn,
      signOut: ShrecknetShell._signOut,
      openHome: ShrecknetShell._openHome,
      openFolder: ShrecknetShell._openFolder,
      openSceneCollection: ShrecknetShell._openSceneCollection,
      openKnowledgeWorlds: ShrecknetShell._openKnowledgeWorlds,
      openChronicles: ShrecknetShell._openChronicles,
      openElder: ShrecknetShell._openElder,
      openKnowledgeWorld: ShrecknetShell._openKnowledgeWorld,
      openKnowledgeSource: ShrecknetShell._openKnowledgeSource,
      queryKnowledge: ShrecknetShell._queryKnowledge,
      toggleKnowledgePane: ShrecknetShell._toggleKnowledgePane,
      copyKnowledgeAnswer: ShrecknetShell._copyKnowledgeAnswer,
      shareKnowledgeAnswer: ShrecknetShell._shareKnowledgeAnswer,
      askElder: ShrecknetShell._askElder,
      clearElderContext: ShrecknetShell._clearElderContext,
      createChronicle: ShrecknetShell._createChronicle,
      openChronicle: ShrecknetShell._openChronicle,
      saveChronicle: ShrecknetShell._saveChronicle,
      deleteChronicle: ShrecknetShell._deleteChronicle,
      reloadChronicle: ShrecknetShell._reloadChronicle,
      showChronicleOnChat: ShrecknetShell._showChronicleOnChat,
      showChronicleImage: ShrecknetShell._showChronicleImage,
      openInstance: ShrecknetShell._openInstance,
      openScene: ShrecknetShell._openScene,
      search: ShrecknetShell._search,
      searchScenes: ShrecknetShell._searchScenes,
      selectTextSource: ShrecknetShell._selectTextSource,
      showOnChat: ShrecknetShell._showOnChat,
      toggleReaderFocus: ShrecknetShell._toggleReaderFocus,
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
    this.readerFocus = false;
    this.explorerPanelState = new Map();
    this.folders = [];
    this.activeTextSourceByEntity = new Map();
    this.resource = { kind: "home", title: this.connection.worldName || "Shrecknet" };
    this.pageCache = new Map();
    this.relationshipCardsByInstance = new Map();
    this.propertyNamesByEntityDefinition = new Map();
    this.atlasDefinitionsByOntology = new Map();
    this.resourceObjectUrls = [];
    this.resourceObjectUrlsByKey = new Map();
    this.assetPathByObjectUrl = new Map();
    this.uploadedChronicleAssets = new Map();
    this.chronicleRecipients = [];
    this.elderAgentId = null;
    this.elderChatId = null;
    this.elderHistory = [];
    this.knowledgeSessions = new Map();
    this.knowledgeWorldChoices = [];
    this.knowledgeSources = [];
    this.knowledgeResults = null;
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
      readerFocus: this.readerFocus,
      configured: Boolean(this.connection.serverUrl && this.connection.worldId),
      canConfigure: game.user.isGM,
      authenticated: Boolean(this.session?.user),
      user: this.session?.user,
      connection: this.connection,
      worldChoices: this.worldChoices,
      hasWorldChoices: this.worldChoices.length > 0,
      connectionTested: this.connectionTested,
      resource: await this._presentResource(),
      isSceneWorkspace: ["scene", "sceneCollection"].includes(this.resource.kind),
      isKnowledgeWorkspace: ["knowledgeWorlds", "knowledgeLibrary", "knowledgeSource", "knowledgeResults"].includes(this.resource.kind),
      isChroniclesWorkspace: ["chronicleLibrary", "chronicle"].includes(this.resource.kind),
      isElderWorkspace: this.resource.kind === "elder",
      isAtlasWorkspace: !["sceneCollection", "scene", "knowledgeWorlds", "knowledgeLibrary", "knowledgeSource", "knowledgeResults", "chronicleLibrary", "chronicle", "elder"].includes(this.resource.kind),
      shreckWorldUrl: this._serviceUrl("shreckWorld"),
      shreckRpgUrl: this._serviceUrl("shreckRpg"),
      canEditChronicle: Boolean(this.resource.chronicle?.capabilities?.edit),
      canDeleteChronicle: Boolean(this.resource.chronicle?.capabilities?.delete),
      canShareChronicle: Boolean(this.resource.chronicle?.capabilities?.share),
      chronicleRecipients: this.chronicleRecipients.map((recipient) => ({
        ...recipient,
        selected: (this.resource.chronicle?.shared_with || []).includes(recipient.id),
      })),
      elderAgents: (this.resource.agents || []).map((agent) => ({
        ...agent,
        selected: String(agent.id) === String(this.elderAgentId),
      })),
      elderIdentity: (() => {
        const agent = (this.resource.agents || []).find((a) => String(a.id) === String(this.elderAgentId));
        return agent ? { ...agent, avatarUrl: safeImageUrl(this.client.resolveUrl(agent.avatar_url)) } : null;
      })(),
      elderContext: this.elderContextInstanceId ? { id: this.elderContextInstanceId, name: this.elderContextName } : null,
      elderDraft: this.elderDraft || "",
      elderHistory: this.elderHistory.map((message) => ({ ...message, isUser: message.role === "user", citations: presentElderSources(message.citations) })),
      isAskingElder: Boolean(this.isAskingElder),
      knowledgeResults: this.knowledgeResults,
      knowledge: this._knowledgeContext(),
      canShowChronicleImages: game.user.isGM,
      folders: this.folders,
      notice: this.notice,
      isLoading: this.isLoading,
      canGoBack: this.history.canGoBack,
      canGoForward: this.history.canGoForward,
      canShowPlayers: game.user.isGM,
    };
  }

  _onRender(context, options) {
    super._onRender(context, options);
    const root = this.element instanceof HTMLElement ? this.element : this.element?.[0];
    const explorer = root?.querySelector(".shrecknet-atlas-explorer");
    root?.querySelectorAll("details[data-explorer-panel]").forEach((panel) => {
      const key = panel.dataset.explorerPanel;
      panel.open = this.explorerPanelState.get(key) ?? (explorer.clientWidth >= 1100);
      panel.addEventListener("toggle", () => this.explorerPanelState.set(key, panel.open));
    });
    root?.querySelectorAll("form[data-shrecknet-submit]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const action = form.dataset.shrecknetSubmit;
        const handler = this.constructor.DEFAULT_OPTIONS.actions[action];
        if (typeof handler === "function") handler.call(this, event, form);
      });
    });
    const knowledge = this.resource.world && this.knowledgeSessions.get(this.resource.world.id);
    root?.querySelector("[data-knowledge-draft]")?.addEventListener("input", (event) => { knowledge.draft = event.target.value; });
    root?.querySelector("[data-knowledge-search]")?.addEventListener("input", (event) => {
      const search = event.target.value.toLocaleLowerCase();
      root.querySelectorAll("[data-book-title]").forEach((card) => { card.hidden = !card.dataset.bookTitle.toLocaleLowerCase().includes(search); });
    });
    root?.querySelectorAll("[data-knowledge-source]").forEach((input) => input.addEventListener("change", () => {
      if (knowledge.busy) return;
      if (input.checked) knowledge.selected.add(input.value); else knowledge.selected.delete(input.value);
      void this.render();
    }));
    const log = root?.querySelector(".shrecknet-knowledge-log");
    if (log) log.scrollTop = log.scrollHeight;
    const draft = root?.querySelector("[data-elder-draft]");
    draft?.addEventListener("input", () => { this.elderDraft = draft.value; });
    const conversation = root?.querySelector(".shrecknet-elder-history");
    if (conversation) conversation.scrollTop = conversation.scrollHeight;
    root?.querySelectorAll("select[data-elder-select]").forEach((select) => {
      select.addEventListener("change", (event) => this.constructor._selectElder.call(this, event, select));
    });
    root?.querySelectorAll("input[data-chronicle-upload]").forEach((input) => {
      input.addEventListener("change", (event) => this._uploadChronicleImage(event, input));
    });
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

  async _presentEntity(entity, relationshipCards, ontologyId, pageImageUrl = null) {
    const text = nonBlank(entity.text);
    const generatedText = nonBlank(entity.autogenerated_text);
    const hasBothTextSources = Boolean(text && generatedText);
    const activeTextSource = selectedTextSource(entity, this.activeTextSourceByEntity);
    const avatarUrl = safeImageUrl(this.client.resolveUrl(entity.node_avatar_url));
    return {
      ...entity,
      avatarUrl,
      showAvatar: Boolean(avatarUrl && avatarUrl !== pageImageUrl),
      textHtml: text ? (await this._presentDocument(await this._enrichHtml(text))).contentHtml : null,
      generatedTextHtml: generatedText ? (await this._presentDocument(await this._enrichHtml(generatedText))).contentHtml : null,
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
      imageUrl: safeImageUrl(this.client.resolveUrl(image)),
    };
  }

  async _loadAtlasDefinitions(instance) {
    const key = instance.ontology_id;
    let definitions = this.atlasDefinitionsByOntology.get(key);
    if (!definitions) {
      const types = await this.client.listEntityTypes(key, { displayedOnly: false });
      definitions = {
        types: new Map(types.map((type) => [String(type.id), type.name])),
        relationships: new Map(), loadedEntities: new Set(),
      };
    }
    const ids = [...new Set(instance.entities.map((entity) => entity.definition_id))]
      .filter((id) => !definitions.loadedEntities.has(id));
    const relationships = await Promise.all(ids.map((id) => this.client.listEntityRelationships(key, id)));
    relationships.flat().forEach((item) => definitions.relationships.set(String(item.id), item.name));
    ids.forEach((id) => definitions.loadedEntities.add(id));
    this.atlasDefinitionsByOntology.set(key, definitions);
    return definitions;
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
    const [resolved, definitions] = await Promise.all([
      this.client.resolveEntities(instance.ontology_id, targetIds), this._loadAtlasDefinitions(instance),
    ]);
    const cards = new Map(resolved.results.map((record) => [record.entity_instance_id, {
      instanceId: record.instance_id,
      name: record.instance_name || record.entity_alias || "Untitled record",
      typeId: record.entity_definition_id,
      typeName: definitions.types.get(String(record.entity_definition_id)) || "Unknown type",
      imageUrl: this.client.resolveUrl(record.avatar_url),
    }]));
    this.relationshipCardsByInstance.set(instance.instance_id, cards);
    return cards;
  }

  async _enrichHtml(html) {
    const editor = globalThis.TextEditor || foundry.applications?.ux?.TextEditor?.implementation;
    return editor?.enrichHTML ? editor.enrichHTML(html, { async: true }) : html;
  }

  _objectUrl(key, blob) {
    if (this.resourceObjectUrlsByKey.has(key)) return this.resourceObjectUrlsByKey.get(key);
    const url = URL.createObjectURL(blob);
    this.resourceObjectUrls.push(url);
    this.resourceObjectUrlsByKey.set(key, url);
    return url;
  }

  async _loadObjectUrl(key, loadBlob) {
    if (this.resourceObjectUrlsByKey.has(key)) return this.resourceObjectUrlsByKey.get(key);
    return this._objectUrl(key, await loadBlob());
  }

  async _presentChronicle(chronicle) {
    return this._presentDocument(chronicle.content, { chronicle, baseUrl: this._serviceUrl("shreckRpg") });
  }

  // Reader and Chronicle markup share a safe HTML subset; only Chronicles
  // resolve protected assets using their owning document and player session.
  async _presentDocument(html, { chronicle = null, baseUrl = this.connection.serverUrl } = {}) {
    const document = new DOMParser().parseFromString(html || "", "text/html");
    const allowedTags = new Set(["A", "B", "BLOCKQUOTE", "BR", "CODE", "DEL", "DIV", "EM", "H1", "H2", "H3", "H4", "H5", "H6", "HR", "I", "IMG", "INS", "LI", "OL", "P", "PRE", "S", "SPAN", "STRONG", "TABLE", "TBODY", "TD", "TH", "THEAD", "TR", "U", "UL"]);
    const images = [];
    for (const element of [...document.body.querySelectorAll("*")]) {
      if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM"].includes(element.tagName)) {
        element.remove();
        continue;
      }
      if (!allowedTags.has(element.tagName)) {
        element.replaceWith(...element.childNodes);
        continue;
      }
      for (const attribute of [...element.attributes]) {
        if (!["href", "src", "alt", "title", "width", "height", "colspan", "rowspan"].includes(attribute.name.toLowerCase())) {
          element.removeAttribute(attribute.name);
        }
      }
      const linkValue = element.getAttribute("href");
      if (linkValue) {
        try {
          const url = new URL(linkValue, baseUrl);
          if (!["https:", "mailto:"].includes(url.protocol) || url.username || url.password) element.removeAttribute("href");
          else element.setAttribute("href", url.href);
        } catch (_) { element.removeAttribute("href"); }
      }
      if (element.tagName !== "IMG") continue;
      const source = element.getAttribute("src") || "";
      const match = source.match(/^\/chronicles\/(\d+)\/assets\/([a-f0-9]{32})$/);
      if (chronicle && match && Number(match[1]) === Number(chronicle.id)) {
        const objectUrl = await this._loadObjectUrl(source, () =>
          this.client.getChronicleAsset(this._serviceUrl("shreckRpg"), chronicle.id, source),
        );
        this.assetPathByObjectUrl.set(objectUrl, source);
        element.setAttribute("src", objectUrl);
        images.push({ url: objectUrl, sourceUrl: source, title: element.getAttribute("alt") || chronicle.title });
      } else {
        try {
          const url = chronicle ? new URL(source) : new URL(source, baseUrl);
          if (url.protocol !== "https:" || url.username || url.password) element.remove();
          else element.setAttribute("src", url.href);
        } catch (_) { element.remove(); }
      }
    }
    return { contentHtml: document.body.innerHTML, images };
  }

  _chronicleStoredHtml(html) {
    const document = new DOMParser().parseFromString(html || "", "text/html");
    for (const image of document.body.querySelectorAll("img")) {
      const source = this.assetPathByObjectUrl.get(image.getAttribute("src"));
      if (source) image.setAttribute("src", source);
    }
    return document.body.innerHTML;
  }

  async _presentResource() {
    if (this.resource.kind === "knowledgeLibrary") {
      const resource = this.resource;
      resource.sources = await Promise.all(resource.sources.map(async (source) => {
        if (!source.cover_url) return source;
        try {
          const coverUrl = await this._loadObjectUrl(
            `shreckworld:${resource.world.id}:${source.id}`,
            () => this.client.getKnowledgeCover(this._serviceUrl("shreckWorld"), resource.world.id, source.id),
          );
          return { ...source, coverUrl };
        } catch (_) { return { ...source, coverUrl: null }; }
      }));
      return this.resource;
    }
    if (this.resource.kind === "chronicle" && this.resource.chronicle) {
      const { contentHtml, images } = await this._presentChronicle(this.resource.chronicle);
      return { ...this.resource, contentHtml, images };
    }
    if (this.resource.kind === "knowledgeSource" && this.resource.source?.cover_url) {
      const source = this.resource.source;
      const cover = await this._loadObjectUrl(
        `shreckworld:${this.resource.world.id}:${source.id}`,
        () => this.client.getKnowledgeCover(this._serviceUrl("shreckWorld"), this.resource.world.id, source.id),
      );
      return { ...this.resource, source: { ...source, coverUrl: cover } };
    }
    if (this.resource.kind !== "instance") return this.resource;
    const instance = this.resource.instance;
    const relationshipCards = await this._resolveRelationshipCards(instance);
    await this._loadPropertyNames(instance);
    const pageCard = this._presentPageCard(instance);
    const definitions = await this._loadAtlasDefinitions(instance);
    const entities = await Promise.all(instance.entities.map((entity) =>
      this._presentEntity(entity, relationshipCards, instance.ontology_id, pageCard.imageUrl),
    ));
    const related_content = instance.related_content?.map((item) => ({
      ...item, displayName: item.name || "Untitled record", imageUrl: this.client.resolveUrl(item.image),
    })) || [];
    return {
      ...this.resource,
      instance: {
        ...instance,
        displayName: pageCard.displayName,
        pageImageUrl: pageCard.imageUrl,
        typeNames: [...new Set(instance.entities.map((entity) => definitions.types.get(String(entity.definition_id))).filter(Boolean))].join(" · "),
        relationshipGroups: groupRelationships(instance.entities, relationshipCards, definitions.relationships),
        entities,
        related_content,
        scenes: instance.scenes || [],
      },
    };
  }

  async close(options = {}) {
    this.elderRequestToken = null;
    this._releaseObjectUrls();
    return super.close(options);
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

  _serviceUrl(service) {
    const value = service === "shreckWorld" ? this.connection.shreckWorldUrl : this.connection.shreckRpgUrl;
    const path = service === "shreckWorld" ? "/shreckworld-api" : "/api/shreckrpg";
    return ShrecknetClient.normalizeServiceUrl(value)
      || ShrecknetClient.serviceBaseFromServer(this.connection.serverUrl, path);
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
    this._releaseObjectUrls();
    this.resource = { kind: "home", title: world.name, breadcrumbs: [this._homeBreadcrumb()], folders: this.folders, statusText: this.folders.length + " directories · WORLD INDEX LOADED" };
    if (push) this.history.navigate(this.resource);
  }

  async _loadSceneCollection({ query = "", push = true } = {}) {
    const world = await this.client.getWorld(this.connection.worldId);
    const summaries = (await Promise.all(world.ontology_ids.map((id) => this.client.listSceneSummaries(id, { query })))).flat();
    const groups = this._groupSceneSummaries(summaries);
    this._releaseObjectUrls();
    this.resource = {
      kind: "sceneCollection", title: "Narrative Scenes", query,
      breadcrumbs: [this._homeBreadcrumb(true), { label: "Narrative Scenes" }], groups,
    };
    if (push) this.history.navigate(this.resource);
  }

  _groupSceneSummaries(summaries) {
    const groups = [...summaries.reduce((byPage, scene) => {
      const group = byPage.get(scene.instance_id) || {
        instanceId: scene.instance_id, name: scene.source_page_name,
        imageUrl: this.client.resolveUrl(scene.source_page_image), scenes: [],
      };
      group.scenes.push(scene);
      byPage.set(scene.instance_id, group);
      return byPage;
    }, new Map()).values()];
    return groups;
  }

  async _handleError(error) {
    if (error instanceof ShrecknetApiError && error.isAuthenticationError) {
      this.knowledgeSessions.clear();
    this.elderDraft = "";
    this.elderHistory = [];
    this.elderChatId = null;
    this.elderRequestToken = null;
    this.client.clearSession();
      this.session = null;
      this.folders = [];
      this.chronicleRecipients = [];
      this._releaseObjectUrls();
      this.uploadedChronicleAssets.clear();
      this.resource = { kind: "home", title: this.connection.worldName };
      this.history = new NavigationHistory(this.resource);
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
    const selected = app.worldChoices.find((world) => String(world.id) === String(worldId));
    if (!selected) return app._handleError(new Error("Test the connection and select a Shrecknet World first."));
    try {
      app.client.setServer(data.get("serverUrl"));
      await app.client.ping();
      const shreckWorldUrl = ShrecknetClient.normalizeServiceUrl(
        data.get("shreckWorldUrl") || ShrecknetClient.serviceBaseFromServer(app.client.serverUrl, "/shreckworld-api"),
      );
      const shreckRpgUrl = ShrecknetClient.normalizeServiceUrl(
        data.get("shreckRpgUrl") || ShrecknetClient.serviceBaseFromServer(app.client.serverUrl, "/api/shreckrpg"),
      );
      await saveConnectionState({ serverUrl: app.client.serverUrl, worldId, worldName: selected.name, shreckWorldUrl, shreckRpgUrl });
      app.connection = getConnectionState();
      app.client.setServer(app.connection.serverUrl);
      app.notice = "Shrecknet connection saved.";
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  static async _saveServiceUrls(event) {
    const app = this;
    if (!game.user.isGM) return app._handleError(new Error("Only a Foundry GM can configure service URLs."));
    try {
      const data = app._form(event);
      const shreckWorldUrl = ShrecknetClient.normalizeServiceUrl(data.get("shreckWorldUrl"));
      const shreckRpgUrl = ShrecknetClient.normalizeServiceUrl(data.get("shreckRpgUrl"));
      if (!shreckWorldUrl || !shreckRpgUrl) throw new Error("Both ShreckWorld and ShreckRPG API URLs are required.");
      await saveServiceUrls({ shreckWorldUrl, shreckRpgUrl });
      app.connection = getConnectionState();
      app._releaseObjectUrls();
      app.notice = "Connected service URLs saved.";
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
    this.elderDraft = "";
    this.elderHistory = [];
    this.elderChatId = null;
    this.elderRequestToken = null;
    this.client.clearSession();
    this.pageCache.clear();
    this.relationshipCardsByInstance.clear();
    this.propertyNamesByEntityDefinition.clear();
    this.atlasDefinitionsByOntology.clear();
    this.activeTextSourceByEntity.clear();
    this.session = null;
    this.folders = [];
    this._releaseObjectUrls();
    this.uploadedChronicleAssets.clear();
    this.resource = { kind: "home", title: this.connection.worldName };
    this.history = new NavigationHistory(this.resource);
    await this.render();
  }

  static async _openHome() { try { await this._withLoading(() => this._loadHome()); await this.render(); } catch (error) { await this._handleError(error); } }

  static async _openKnowledgeWorlds() {
    try {
      const worlds = await this._withLoading(() => this.client.listKnowledgeWorlds(this._serviceUrl("shreckWorld")));
      this.knowledgeWorldChoices = worlds;
      this._releaseObjectUrls();
      this.resource = {
        kind: "knowledgeWorlds", title: "ShreckWorld", worlds,
        breadcrumbs: [this._homeBreadcrumb(true), { label: "ShreckWorld" }],
      };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _openKnowledgeWorld(event, target) {
    const worldId = getActionTarget(event, target).dataset.worldId;
    if (!worldId) return this._handleError(new Error("The selected ShreckWorld is missing its identifier."));
    try {
      const world = this.knowledgeWorldChoices.find((item) => item.id === worldId)
        || { id: worldId, name: "ShreckWorld" };
      const sources = await this._withLoading(() => this.client.listKnowledgeSources(this._serviceUrl("shreckWorld"), worldId));
      this.knowledgeSources = sources;
      this.resource = {
        kind: "knowledgeLibrary", title: world.name, world, sources: this.knowledgeSources,
        breadcrumbs: [this._homeBreadcrumb(true), { label: "ShreckWorld", action: "openKnowledgeWorlds" }, { label: world.name }],
      };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  _knowledgeSession(worldId) {
    if (!this.knowledgeSessions.has(worldId)) this.knowledgeSessions.set(worldId, { selected: new Set(), turns: [], draft: "", pane: "companion", busy: false });
    return this.knowledgeSessions.get(worldId);
  }

  _knowledgeContext() {
    if (!this.resource.world || !this.resource.kind.startsWith("knowledge")) return null;
    const world = this.resource.world;
    const state = this._knowledgeSession(world.id);
    const sources = this.resource.sources || [];
    return {
      ...state, showBooks: state.pane === "books",
      manageUrl: knowledgeReaderUrl(this.connection.serverUrl, world.id),
      scope: state.selected.size ? sources.filter((s) => state.selected.has(s.id)).map((s) => s.title).join(", ") : "All books",
      books: sources.map((source) => ({ ...source,
        selected: state.selected.has(source.id), readerUrl: knowledgeReaderUrl(this.connection.serverUrl, world.id, source.id),
        readiness: source.index_status === "ready" && source.embedding_status === "ready" ? "Indexed · query availability checked when searching" : `${source.index_status} · ${source.embedding_status}`,
      })),
      turns: state.turns.map((turn, index) => ({ ...turn, index,
        citations: turn.citations.map((citation) => ({ ...citation,
          readerUrl: citation.library_item_id ? knowledgeReaderUrl(this.connection.serverUrl, world.id, citation.library_item_id) : null,
        })),
      })),
    };
  }

  static async _openKnowledgeSource(event, target) {
    const id = getActionTarget(event, target).dataset.sourceId;
    if (this.knowledgeSources.some((source) => source.id === id)) window.open(knowledgeReaderUrl(this.connection.serverUrl, this.resource.world.id, id), "_blank", "noopener,noreferrer");
  }

  static async _toggleKnowledgePane() {
    const state = this._knowledgeSession(this.resource.world.id);
    state.pane = state.pane === "books" ? "companion" : "books";
    await this.render();
  }

  static async _copyKnowledgeAnswer(event, target) {
    try {
      const turn = this._knowledgeSession(this.resource.world.id).turns[Number(getActionTarget(event, target).dataset.turn)];
      if (turn) await navigator.clipboard.writeText(formatKnowledgeTurn(turn));
    } catch (error) { await this._handleError(error); }
  }

  static async _shareKnowledgeAnswer(event, target) {
    try {
      const turn = this._knowledgeSession(this.resource.world.id).turns[Number(getActionTarget(event, target).dataset.turn)];
      if (!turn) return;
      if (game.user.can && !game.user.can("MESSAGE_CREATE")) throw new Error("You do not have permission to create Foundry chat messages.");
      const container = document.createElement("div");
      container.textContent = formatKnowledgeTurn(turn);
      await ChatMessage.create({ content: `<div style="white-space: pre-wrap">${container.innerHTML}</div>`, speaker: ChatMessage.getSpeaker() });
    } catch (error) { await this._handleError(error); }
  }

  static async _queryKnowledge(event, target) {
    const query = String(this._form(event, target).get("query") || "").trim();
    const world = this.resource.world;
    if (!query || !world) return this._handleError(new Error("Enter a question and select a ShreckWorld first."));
    const state = this._knowledgeSession(world.id);
    if (state.busy) return;
    const sourceIds = [...state.selected];
    const session = this.session;
    state.draft = query;
    state.busy = true;
    this.notice = null;
    await this.render();
    try {
      const result = await this.client.queryKnowledge(this._serviceUrl("shreckWorld"), world.id, query, sourceIds, { answer: true });
      if (this.session !== session) return;
      state.turns.push(presentKnowledgeTurn(query, result, sourceIds));
      state.draft = "";
    } catch (error) { if (this.session === session) await this._handleError(error); }
    finally { state.busy = false; await this.render(); }
  }

  static async _clearElderContext() {
    if (this.isAskingElder) return;
    this.elderContextInstanceId = null;
    this.elderContextName = null;
    await this.render();
  }

  static async _openElder() {
    if (this.resource.kind === "elder" || this.isAskingElder) return;
    const app = this;
    try {
      const world = await app._withLoading(() => app.client.getWorld(app.connection.worldId));
      const lists = await Promise.all(world.ontology_ids.map((id) => app.client.listElders(id)));
      const agents = [...new Map(lists.flatMap((list) => Array.isArray(list) ? list : list?.elders || [])
        .map((agent) => [String(agent.id), agent])).values()];
      app.elderAgentId = agents.some((agent) => String(agent.id) === String(app.elderAgentId))
        ? app.elderAgentId
        : agents[0]?.id || null;
      app.elderChatId = null;
      app.elderHistory = [];
      app.elderContextInstanceId = app.resource.instance?.instance_id
        || app.resource.scene?.instance_id
        || null;
      app.elderContextName = app.resource.title || "Focused Atlas page";
      app.resource = {
        kind: "elder", title: "Elder Companion", agents,
        breadcrumbs: [app._homeBreadcrumb(true), { label: "Elder Companion" }],
      };
      if (app.elderAgentId) await app._loadElderHistory(app.elderAgentId);
      app._releaseObjectUrls();
      app.history.navigate(app.resource);
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  async _loadElderHistory(agentId) {
    const chatsResponse = await this.client.listElderChats(agentId);
    const chats = Array.isArray(chatsResponse) ? chatsResponse : chatsResponse?.chats || [];
    this.elderChatId = chats[0]?.id || null;
    if (!this.elderChatId) {
      this.elderHistory = [];
      return;
    }
    const detail = await this.client.getElderChat(this.elderChatId);
    const history = detail?.history || detail?.messages || detail?.chat?.history || [];
    this.elderHistory = presentElderHistory(history);
  }

  static async _selectElder(event, target) {
    if (this.isAskingElder) return;
    this.elderDraft = "";
    const agentId = target.value;
    this.elderAgentId = agentId || null;
    this.elderChatId = null;
    this.elderHistory = [];
    try {
      if (this.elderAgentId) await this._withLoading(() => this._loadElderHistory(this.elderAgentId));
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _askElder(event, target) {
    if (this.isAskingElder) return;
    const query = String(this._form(event, target).get("query") || "").trim();
    if (!query || !this.elderAgentId) return this._handleError(new Error("Choose an Elder and enter a question."));
    const requestToken = Symbol("elder-question");
    this.elderRequestToken = requestToken;
    const agentId = this.elderAgentId;
    const session = this.session;
    const instanceId = this.elderContextInstanceId;
    let chatId = this.elderChatId;
    const isActive = () => this.elderRequestToken === requestToken && this.session === session && this.resource.kind === "elder"
      && String(this.elderAgentId) === String(agentId) && this.elderChatId === chatId;
    this.isAskingElder = true;
    this.notice = null;
    try {
      await this._withLoading(async () => {
        if (!chatId) {
          const chat = await this.client.createElderChat(agentId, this.connection.worldName);
          const createdChatId = chat.id || chat.chat_id;
          if (!createdChatId) throw new Error("Shrecknet did not return an Elder chat identifier.");
          if (this.session !== session || this.resource.kind !== "elder" || String(this.elderAgentId) !== String(agentId)) return;
          chatId = createdChatId;
          this.elderChatId = chatId;
        }
        const history = await submitElderTurn(this.client, {
          agentId, chatId, query, instanceId, isActive,
          onRecover: () => {
            if (isActive()) {
              this.notice = "The connection was interrupted. Checking this conversation for Elder's saved reply…";
              void this.render();
            }
          },
        });
        if (history && isActive()) {
          this.elderHistory = presentElderHistory(history);
          this.elderDraft = "";
          this.notice = null;
        }
      });
    } catch (error) { if (isActive()) await this._handleError(error); }
    finally {
      this.isAskingElder = false;
      await this.render();
    }
  }

  static async _openChronicles() {
    try {
      const chronicles = await this._withLoading(() => this.client.listChronicles(this._serviceUrl("shreckRpg")));
      this._releaseObjectUrls();
      this.resource = {
        kind: "chronicleLibrary", title: "Chronicles",
        chronicles: Array.isArray(chronicles) ? chronicles : [],
        breadcrumbs: [this._homeBreadcrumb(true), { label: "Chronicles" }],
      };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _createChronicle(event, target) {
    const title = String(this._form(event, target).get("title") || "").trim();
    if (!title) return this._handleError(new Error("Enter a title for the Chronicle."));
    try {
      const chronicle = await this._withLoading(() => this.client.createChronicle(this._serviceUrl("shreckRpg"), title));
      this.chronicleRecipients = chronicle.capabilities?.share
        ? await this.client.listChronicleRecipients(this._serviceUrl("shreckRpg"))
        : [];
      this._releaseObjectUrls();
      this.resource = { kind: "chronicle", title: chronicle.title, chronicle, breadcrumbs: [this._homeBreadcrumb(true), { label: "Chronicles", action: "openChronicles" }, { label: chronicle.title }] };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _openChronicle(event, target) {
    const id = getActionTarget(event, target).dataset.chronicleId;
    if (!id) return this._handleError(new Error("The selected Chronicle is missing its identifier."));
    try {
      const chronicle = await this._withLoading(() => this.client.getChronicle(this._serviceUrl("shreckRpg"), id));
      this.chronicleRecipients = chronicle.capabilities?.share
        ? await this.client.listChronicleRecipients(this._serviceUrl("shreckRpg"))
        : [];
      this._releaseObjectUrls();
      this.resource = { kind: "chronicle", title: chronicle.title, chronicle, breadcrumbs: [this._homeBreadcrumb(true), { label: "Chronicles", action: "openChronicles" }, { label: chronicle.title }] };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  _releaseObjectUrls() {
    this.resourceObjectUrls.forEach((url) => URL.revokeObjectURL(url));
    this.resourceObjectUrls = [];
    this.resourceObjectUrlsByKey.clear();
    this.assetPathByObjectUrl.clear();
  }

  _showInlineNotice(message) {
    const root = this.element instanceof HTMLElement ? this.element : this.element?.[0];
    const notice = root?.querySelector("[data-workspace-notice]");
    if (notice) {
      notice.textContent = message;
      notice.hidden = false;
    } else {
      this.notice = message;
      this.render();
    }
  }

  _showWorkspaceError(error, fallback) {
    if (error instanceof ShrecknetApiError && error.isAuthenticationError) return this._handleError(error);
    this._showInlineNotice(error.message || fallback);
  }

  static async _saveChronicle(event, target) {
    const form = target.closest("form");
    const chronicle = this.resource.chronicle;
    if (!chronicle?.capabilities?.edit) return this._showInlineNotice("You do not have permission to edit this Chronicle.");
    const title = String(new FormData(form).get("title") || "").trim();
    const editor = form.querySelector("[data-chronicle-editor]");
    if (!title || !editor) return this._showInlineNotice("A Chronicle title and document are required.");
    const sharedWith = chronicle.capabilities.share
      ? [...form.querySelectorAll('input[name="recipientIds"]:checked')].map((input) => Number(input.value))
      : chronicle.shared_with || [];
    if (sharedWith.some((id) => !Number.isInteger(id) || id < 1)) {
      return this._showInlineNotice("The Chronicle sharing list contains an invalid recipient.");
    }
    try {
      const saved = await this.client.saveChronicle(
        this._serviceUrl("shreckRpg"), chronicle, title, this._chronicleStoredHtml(editor.innerHTML), sharedWith,
      );
      this.resource = { ...this.resource, title: saved.title, chronicle: saved };
      this.notice = null;
      await this.render();
    } catch (error) {
      if (error instanceof ShrecknetApiError && error.isAuthenticationError) return this._handleError(error);
      this._showInlineNotice(error.status === 409
        ? "This Chronicle was changed elsewhere. Reload it before saving to avoid overwriting newer edits."
        : error.message || "Unable to save this Chronicle.");
    }
  }

  static async _reloadChronicle() {
    const app = this;
    try {
      const current = await app.client.getChronicle(app._serviceUrl("shreckRpg"), app.resource.chronicle.id);
      app.resource = { ...app.resource, title: current.title, chronicle: current };
      if (current.capabilities?.share) {
        app.chronicleRecipients = await app.client.listChronicleRecipients(app._serviceUrl("shreckRpg"));
      }
      app.notice = "Chronicle reloaded.";
      await app.render();
    } catch (error) { await app._handleError(error); }
  }

  static async _deleteChronicle() {
    const app = this;
    if (!app.resource.chronicle?.capabilities?.delete) {
      return app._showInlineNotice("You do not have permission to delete this Chronicle.");
    }
    const confirmed = await Dialog.confirm({
      title: "Delete Chronicle",
      content: `<p>Delete “${foundry.utils.escapeHTML(app.resource.chronicle.title)}”?</p>`,
    });
    if (!confirmed) return;
    try {
      await app.client.deleteChronicle(app._serviceUrl("shreckRpg"), app.resource.chronicle.id);
      await ShrecknetShell._openChronicles.call(app);
    } catch (error) { await app._handleError(error); }
  }

  async _uploadChronicleForFoundry(sourceUrl) {
    if (this.uploadedChronicleAssets.has(sourceUrl)) return this.uploadedChronicleAssets.get(sourceUrl);
    const chronicle = this.resource.chronicle;
    const blob = await this.client.getChronicleAsset(this._serviceUrl("shreckRpg"), chronicle.id, sourceUrl);
    const extension = ({ "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp" })[blob.type];
    if (!extension) throw new Error("This Chronicle image format cannot be shared in Foundry.");
    const file = new File([blob], `chronicle-${chronicle.id}-${sourceUrl.split("/").at(-1)}.${extension}`, { type: blob.type });
    const uploaded = await FilePicker.upload("data", `shrecknet/chronicles/${chronicle.id}`, file);
    if (!uploaded?.path) throw new Error("Foundry did not return a stored path for the Chronicle image.");
    this.uploadedChronicleAssets.set(sourceUrl, uploaded.path);
    return uploaded.path;
  }

  static async _showChronicleImage(event, target) {
    const app = this;
    if (!game.user.isGM) return app._showInlineNotice("Only a Foundry GM can show Chronicle images to all players.");
    const sourceUrl = getActionTarget(event, target).dataset.assetUrl;
    const editor = (app.element instanceof HTMLElement ? app.element : app.element?.[0])?.querySelector("[data-chronicle-editor]");
    const documentContent = `${editor ? app._chronicleStoredHtml(editor.innerHTML) : ""}\n${app.resource.chronicle?.content || ""}`;
    const allowedAsset = new RegExp(`^/chronicles/${Number(app.resource.chronicle?.id)}/assets/[a-f0-9]{32}$`);
    if (!sourceUrl || !allowedAsset.test(sourceUrl) || !documentContent.includes(sourceUrl)) {
      return app._showInlineNotice("This protected Chronicle image is unavailable.");
    }
    try {
      app._showInlineNotice("Preparing the protected image for Foundry players…");
      const imageUrl = await app._uploadChronicleForFoundry(sourceUrl);
      const imageTitle = getActionTarget(event, target).dataset.title || app.resource.title;
      game.socket.emit("module.shrecknet-foundry", { type: "showImage", imageUrl, title: imageTitle });
      app._showInlineNotice("Chronicle image shown to all players.");
    } catch (error) { app._showWorkspaceError(error, "Unable to share the Chronicle image."); }
  }

  static async _showChronicleOnChat() {
    const app = this;
    if (!game.user.isGM) return app._showInlineNotice("Only a Foundry GM can share Chronicle content in chat.");
    const chronicle = app.resource.chronicle;
    const root = app.element instanceof HTMLElement ? app.element : app.element?.[0];
    const editor = root?.querySelector("[data-chronicle-editor]");
    try {
      app._showInlineNotice("Preparing Chronicle content for Foundry chat…");
      const prepared = await app._presentChronicle({
        ...chronicle,
        content: editor ? app._chronicleStoredHtml(editor.innerHTML) : chronicle.content,
      });
      let content = prepared.contentHtml;
      for (const image of prepared.images) {
        const foundryPath = await app._uploadChronicleForFoundry(image.sourceUrl);
        content = content.replaceAll(image.url, foundry.utils.escapeHTML(foundryPath));
      }
      await ChatMessage.create({
        content: `<article class="shrecknet-chat-chronicle"><h2>${foundry.utils.escapeHTML(chronicle.title)}</h2>${content}</article>`,
        speaker: ChatMessage.getSpeaker(),
      });
      app._showInlineNotice("Chronicle shared in Foundry chat.");
    } catch (error) { app._showWorkspaceError(error, "Unable to show this Chronicle in chat."); }
  }

  static async _uploadChronicleImage(event, target) {
    const app = this;
    const file = target.files?.[0];
    if (!file) return;
    if (!app.resource.chronicle?.capabilities?.edit) {
      target.value = "";
      return app._showInlineNotice("You do not have permission to add images to this Chronicle.");
    }
    if (!["image/jpeg", "image/png", "image/gif", "image/webp"].includes(file.type) || file.size > 5 * 1024 * 1024) {
      target.value = "";
      return app._showInlineNotice("Choose a JPEG, PNG, GIF, or WebP image no larger than 5 MiB.");
    }
    try {
      app._showInlineNotice("Uploading image to ShreckRPG…");
      const result = await app.client.uploadChronicleAsset(app._serviceUrl("shreckRpg"), app.resource.chronicle.id, file);
      const editor = target.form?.querySelector("[data-chronicle-editor]");
      if (!editor) throw new Error("The Chronicle editor is unavailable.");
      const blob = await app.client.getChronicleAsset(app._serviceUrl("shreckRpg"), app.resource.chronicle.id, result.url);
      const objectUrl = app._objectUrl(result.url, blob);
      app.assetPathByObjectUrl.set(objectUrl, result.url);
      const image = document.createElement("img");
      image.src = objectUrl;
      image.alt = file.name;
      editor.append(image);
      const images = target.form.querySelector("[data-chronicle-images]");
      if (images && game.user.isGM) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.action = "showChronicleImage";
        button.dataset.assetUrl = result.url;
        button.dataset.title = file.name;
        button.textContent = `Show ${file.name} to players`;
        images.append(button);
      }
      app._showInlineNotice("Image added. Save the Chronicle to keep it.");
      target.value = "";
    } catch (error) {
      app._showWorkspaceError(error, "Unable to upload the Chronicle image.");
    }
  }

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
      const { scene, sourcePage, sourcePageCard } = await this._withLoading(async () => {
        const loadedScene = await this.client.getNarrativeScene(instanceId, sceneId);
        const entityIds = [
          ...loadedScene.relates_to.map((item) => item.entity_instance_id),
          ...loadedScene.milestones.flatMap((milestone) => milestone.relates_to.map((item) => item.entity_instance_id)),
        ];
        if (entityIds.length) {
          const resolved = await this.client.resolveEntities(loadedScene.ontology_id, [...new Set(entityIds)]);
          const byEntityId = new Map(resolved.results.map((item) => [item.entity_instance_id, item]));
          const decorate = (relation) => {
            const record = byEntityId.get(relation.entity_instance_id);
            return {
              ...relation,
              record,
              card: record ? {
                instanceId: record.instance_id,
                name: record.entity_alias || record.instance_name || "Untitled record",
                imageUrl: this.client.resolveUrl(record.avatar_url),
              } : null,
            };
          };
          loadedScene.relates_to = loadedScene.relates_to.map(decorate);
          loadedScene.milestones = loadedScene.milestones.map((milestone) => ({ ...milestone, relates_to: milestone.relates_to.map(decorate) }));
        }
        const page = this.pageCache.get(instanceId) || await this.client.getInstance(instanceId);
        this.pageCache.set(instanceId, page);
        const scenesById = new Map((page.scenes || []).map((item) => [item.id, item]));
        const previousScene = scenesById.get(loadedScene.local_order?.preceded_by_scene_id);
        const nextScene = scenesById.get(loadedScene.local_order?.followed_by_scene_id);
        return {
          scene: { ...loadedScene, previousScene, nextScene },
          sourcePage: page,
          sourcePageCard: this._presentPageCard(page),
        };
      });
      this.resource = { kind: "scene", title: scene.name, scene, sourcePage, sourcePageCard, breadcrumbs: [this._homeBreadcrumb(true), { label: "Narrative Scenes", action: "openSceneCollection" }, { label: scene.name }] };
      this.history.navigate(this.resource);
      await this.render();
    } catch (error) { await this._handleError(error); }
  }

  static async _search(event, target) {
    const app = this;
    const query = String(app._form(event, target).get("query") || "").trim();
    if (!query) return;
    try {
      const { pages, sceneGroups } = await app._withLoading(async () => {
        const world = await app.client.getWorld(app.connection.worldId);
        const [pagesByOntology, scenesByOntology] = await Promise.all([
          Promise.all(world.ontology_ids.map((id) => app.client.searchPages(id, query))),
          Promise.all(world.ontology_ids.map((id) => app.client.listSceneSummaries(id, { query }))),
        ]);
        return {
          pages: [...new Map(pagesByOntology.flat().map((page) => [page.instance_id, page])).values()],
          sceneGroups: app._groupSceneSummaries(scenesByOntology.flat()),
        };
      });
      pages.forEach((page) => app.pageCache.set(page.instance_id, page));
      app.resource = {
        kind: "search", title: `Search: ${query}`, query,
        breadcrumbs: [app._homeBreadcrumb(true), { label: "Search" }],
        instances: pages.map((page) => app._presentPageCard(page)), sceneGroups,
      };
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

  static async _toggleReaderFocus() {
    this.readerFocus = !this.readerFocus;
    await this.render();
  }

  static async _showOnChat(event, target) {
    const app = this;
    if (app.sharingAtlasPage) return;
    if (app.resource.kind !== "instance") return;
    app.sharingAtlasPage = true;
    const selections = new Map(app.activeTextSourceByEntity);
    try {
      const { instance: page } = await app._presentResource();
      const sections = await Promise.all(page.entities.map(async (entity) => {
        const source = selectedTextSource(entity, selections);
        const html = source === "text" ? entity.textHtml : source === "generated" ? entity.generatedTextHtml : "";
        const safe = await app._presentDocument(html);
        return { name: entity.alias || "Record", source, html: safe.contentHtml };
      }));
      // Share a snapshot; related page bodies are never fetched for chat.
      await ChatMessage.create({
        content: renderAtlasChat(page, sections),
        speaker: ChatMessage.getSpeaker(),
      });
    } catch (error) {
      await app._handleError(error);
    } finally {
      app.sharingAtlasPage = false;
    }
  }

  static async _showImageToPlayers(event, target) {
    if (!game.user.isGM) return this._handleError(new Error("Only a Foundry GM can show an image to all players."));
    const { imageUrl, title } = getActionTarget(event, target).dataset;
    if (!imageUrl) return this._handleError(new Error("This Shrecknet image is unavailable."));
    game.socket.emit("module.shrecknet-foundry", { type: "showImage", imageUrl, title: title || "Shrecknet" });
  }

  static async _goBack() {
    const previous = this.history.back();
    const currentId = this.resource.chronicle?.id || this.resource.source?.id;
    const previousId = previous?.chronicle?.id || previous?.source?.id;
    if (["chronicle", "knowledgeSource"].includes(this.resource.kind) && (previous?.kind !== this.resource.kind || previousId !== currentId)) {
      this._releaseObjectUrls();
    }
    this.resource = previous;
    await this.render();
  }
  static async _goForward() {
    const next = this.history.forward();
    const currentId = this.resource.chronicle?.id || this.resource.source?.id;
    const nextId = next?.chronicle?.id || next?.source?.id;
    if (["chronicle", "knowledgeSource"].includes(this.resource.kind) && (next?.kind !== this.resource.kind || nextId !== currentId)) {
      this._releaseObjectUrls();
    }
    this.resource = next;
    await this.render();
  }
}
