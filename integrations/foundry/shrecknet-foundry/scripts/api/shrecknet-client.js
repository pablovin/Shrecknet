const SESSION_KEY = "shrecknet-foundry.session.v2";
const LEGACY_SESSION_KEY = "shrecknet-foundry.session.v1";

export class ShrecknetApiError extends Error {
  constructor(message, status, payload = null) {
    super(message);
    this.name = "ShrecknetApiError";
    this.status = status;
    this.payload = payload;
  }

  get isAuthenticationError() {
    return this.status === 401;
  }
}

/**
 * The only module component allowed to construct Shrecknet HTTP requests.
 * Content methods intentionally call existing Shrecknet public routes.
 */
export class ShrecknetClient {
  constructor({ serverUrl = "", accessToken = null } = {}) {
    this.serverUrl = ShrecknetClient.normalizeServerUrl(serverUrl);
    this.accessToken = accessToken;
  }

  static normalizeServerUrl(value) {
    return String(value || "").trim().replace(/\/+$/, "");
  }

  setServer(serverUrl) {
    this.serverUrl = ShrecknetClient.normalizeServerUrl(serverUrl);
  }

  setAccessToken(accessToken) {
    this.accessToken = accessToken || null;
  }

  static normalizeServiceUrl(value) {
    const raw = String(value || "").trim().replace(/\/+$/, "");
    if (!raw) return "";
    let url;
    try { url = new URL(raw); } catch (_) {
      throw new ShrecknetApiError("Enter a valid ShreckWorld or ShreckRPG API URL.", 0);
    }
    const local = ["localhost", "127.0.0.1", "0.0.0.0"].includes(url.hostname);
    if (!["https:", ...(local ? ["http:"] : [])].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new ShrecknetApiError("Service API URLs must use HTTPS and cannot contain credentials, a query, or a fragment.", 0);
    }
    return `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
  }

  static serviceBaseFromServer(serverUrl, path) {
    try {
      const server = new URL(serverUrl);
      return ShrecknetClient.normalizeServiceUrl(`${server.origin}${path}`);
    } catch (_) {
      return "";
    }
  }

  resolveUrl(value) {
    const url = String(value || "").trim();
    if (!url) return null;
    try {
      const server = new URL(this.serverUrl);
      const mediaPath = url.replace(/^\/?media\//i, "");
      if (/^\/?media\//i.test(url)) return new URL(`/media/${mediaPath}`, server.origin).href;

      const resolved = new URL(url, `${this.serverUrl}/`);
      if (
        resolved.pathname.startsWith("/media/")
        && ["localhost", "127.0.0.1", "0.0.0.0"].includes(resolved.hostname)
      ) {
        return new URL(`${resolved.pathname}${resolved.search}${resolved.hash}`, server.origin).href;
      }
      return resolved.href;
    } catch (_) { return null; }
  }

  loadSession() {
    try {
      const stored = localStorage.getItem(SESSION_KEY);
      const raw = stored || sessionStorage.getItem(LEGACY_SESSION_KEY);
      const session = raw ? JSON.parse(raw) : null;
      if (session?.serverUrl === this.serverUrl && session.accessToken) {
        this.accessToken = session.accessToken;
        if (!stored) localStorage.setItem(SESSION_KEY, JSON.stringify(session));
        return session;
      }
    } catch (_) {
      // Treat corrupt browser-local state as an expired session.
    }
    return null;
  }

  saveSession(user) {
    if (!this.accessToken) return;
    localStorage.setItem(SESSION_KEY, JSON.stringify({
      serverUrl: this.serverUrl,
      accessToken: this.accessToken,
      user,
    }));
  }

  clearSession() {
    this.accessToken = null;
    localStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(LEGACY_SESSION_KEY);
  }

  async request(path, { method = "GET", body, integrationKey, authenticated = true, baseUrl = this.serverUrl } = {}) {
    if (!baseUrl) throw new ShrecknetApiError("A Shrecknet server URL is required.", 0);
    if (authenticated && !this.accessToken) {
      throw new ShrecknetApiError("Sign in to Shrecknet before accessing connected services.", 401);
    }
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (integrationKey) headers["X-Shrecknet-Integration-Key"] = integrationKey;
    if (authenticated && this.accessToken) headers.Authorization = `Bearer ${this.accessToken}`;

    return this._fetchJson(baseUrl, path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  }

  async requestAsset(baseUrl, path) {
    const response = await this._fetch(baseUrl, path, {
      method: "GET",
      headers: { Accept: "image/*", Authorization: `Bearer ${this.accessToken}` },
    });
    if (!response.headers.get("content-type")?.startsWith("image/")) {
      throw new ShrecknetApiError("The requested image is unavailable.", response.status);
    }
    return response.blob();
  }

  async _fetchJson(baseUrl, path, options) {
    const response = await this._fetch(baseUrl, path, options);
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      const detail = payload?.detail;
      const message = typeof detail === "string" ? detail : detail?.message || detail?.code || "Shrecknet request failed.";
      throw new ShrecknetApiError(message, response.status, payload);
    }
    return payload;
  }

  async _fetch(baseUrl, path, options) {
    if (!this.accessToken && !options.headers?.["X-Shrecknet-Integration-Key"] && options.headers?.Authorization) {
      throw new ShrecknetApiError("Sign in to Shrecknet before accessing connected services.", 401);
    }
    const base = ShrecknetClient.normalizeServiceUrl(baseUrl);
    if (!base) throw new ShrecknetApiError("A service API URL is required.", 0);
    if (!String(path).startsWith("/") || String(path).startsWith("//")) {
      throw new ShrecknetApiError("Invalid service API path.", 0);
    }
    let response;
    try {
      response = await fetch(`${base}${path}`, options);
    } catch (_) {
      throw new ShrecknetApiError("Unable to connect to the configured service.", 0);
    }
    return response;
  }

  validateIntegration(integrationKey) {
    return this.request("/integrations/foundry/validate", { method: "POST", integrationKey, authenticated: false });
  }

  ping() { return this.request("/health", { authenticated: false }); }

  listConnectableWorlds(integrationKey) {
    return this.request("/integrations/foundry/worlds", { integrationKey, authenticated: false });
  }

  async login(identifier, password) {
    const token = await this.request("/auth/token", {
      method: "POST",
      body: { username: identifier, password },
      authenticated: false,
    });
    this.setAccessToken(token.access_token);
    const user = await this.getCurrentUser();
    this.saveSession(user);
    return user;
  }

  getCurrentUser() { return this.request("/users/me"); }
  getWorld(worldId) { return this.request(`/worlds/${encodeURIComponent(worldId)}`); }
  getOntology(ontologyId) { return this.request(`/ontologies/${ontologyId}`); }
  listEntityTypes(ontologyId) { return this.request(`/ontologies/${ontologyId}/entities?display_on_world=true`); }
  listEntityProperties(ontologyId, entityDefinitionId) {
    return this.request(`/ontologies/${ontologyId}/entities/${entityDefinitionId}/properties`);
  }

  listInstancePages(ontologyId, entityDefinitionId, { skip = 0, limit = 100, search = "" } = {}) {
    const params = new URLSearchParams({ ontology_id: String(ontologyId), skip: String(skip), limit: String(limit) });
    if (entityDefinitionId !== undefined && entityDefinitionId !== null) {
      params.set("entity_definition_id", String(entityDefinitionId));
    }
    if (search) params.set("search", search);
    return this.request(`/ontology-instances/pages?${params}`);
  }

  searchPages(ontologyId, query) {
    return this.listInstancePages(ontologyId, undefined, { search: query });
  }

  listSceneSummaries(ontologyId, { query = "", skip = 0, limit = 100 } = {}) {
    const params = new URLSearchParams({ ontology_id: String(ontologyId), skip: String(skip), limit: String(limit) });
    if (query) params.set("query", query);
    return this.request(`/ontology-instances/scenes?${params}`);
  }

  getInstance(instanceId) { return this.request(`/ontology-instances/${encodeURIComponent(instanceId)}`); }
  resolveEntities(ontologyId, entityInstanceIds) {
    return this.request("/ontology-instances/resolve-entities", {
      method: "POST", body: { ontology_id: Number(ontologyId), entity_instance_ids: entityInstanceIds },
    });
  }
  listNarrativeScenes(instanceId) { return this.request(`/ontology-instances/${encodeURIComponent(instanceId)}/scenes`); }
  getNarrativeScene(instanceId, sceneId) { return this.request(`/ontology-instances/${encodeURIComponent(instanceId)}/scenes/${encodeURIComponent(sceneId)}`); }

  search(ontologyId, query) {
    const params = new URLSearchParams({ ontology_id: String(ontologyId), query, limit: "20" });
    return this.request(`/ontology-instances/search?${params}`);
  }

  async listElders(ontologyId) {
    const agents = [];
    for (let skip = 0; ; skip += 100) {
      const params = new URLSearchParams({ skip: String(skip), limit: "100" });
      const page = await this.request(`/ontologies/${encodeURIComponent(ontologyId)}/elders?${params}`);
      if (!Array.isArray(page)) throw new ShrecknetApiError("Shrecknet returned an invalid Elder list.", 0, page);
      agents.push(...page);
      if (page.length < 100) return agents;
    }
  }

  listElderChats(agentId) {
    const params = new URLSearchParams({ agent_id: agentId, limit: "100", offset: "0" });
    return this.request(`/jobs/elder/chats/?${params}`);
  }

  getElderChat(chatId) {
    const params = new URLSearchParams({ include_history: "true" });
    return this.request(`/jobs/elder/chats/${encodeURIComponent(chatId)}?${params}`);
  }

  createElderChat(agentId, worldName) {
    return this.request("/jobs/elder/chats/", {
      method: "POST",
      body: { agent_id: agentId, name: `Foundry · ${worldName}`.slice(0, 100), color: "#8B5CF6" },
    });
  }

  askElder(agentId, chatId, query, instanceId = null) {
    return this.request(`/jobs/elder/${encodeURIComponent(agentId)}/query`, {
      method: "POST",
      body: { query, mode: "both", include_trace: false, chat_id: chatId, ...(instanceId ? { instance_id: instanceId } : {}) },
    });
  }

  listKnowledgeWorlds(baseUrl) {
    return this.request("/shreckworlds", { baseUrl });
  }

  listKnowledgeSources(baseUrl, worldId) {
    return this.request(`/shreckworlds/${encodeURIComponent(worldId)}/library-items`, { baseUrl });
  }

  queryKnowledge(baseUrl, worldId, query, libraryItemId = null) {
    return this.request(`/shreckworlds/${encodeURIComponent(worldId)}/query`, {
      baseUrl,
      method: "POST",
      body: { query, top_k: 6, include_trace: false, ...(libraryItemId ? { library_item_id: libraryItemId } : {}) },
    });
  }

  listChronicles(baseUrl) {
    return this.request("/chronicles", { baseUrl });
  }

  getChronicle(baseUrl, id) {
    return this.request(`/chronicles/${encodeURIComponent(id)}`, { baseUrl });
  }

  createChronicle(baseUrl, title) {
    return this.request("/chronicles", { baseUrl, method: "POST", body: { title, content: "<p></p>", ontology_id: null } });
  }

  saveChronicle(baseUrl, note, title, content, sharedWith) {
    return this.request(`/chronicles/${encodeURIComponent(note.id)}`, {
      baseUrl,
      method: "PUT",
      body: {
        expected_revision: note.revision,
        title,
        content,
        ontology_id: note.ontology_id,
        explicit_references: note.explicit_references || [],
        ...(note.capabilities?.share ? { share_user_ids: sharedWith } : {}),
      },
    });
  }

  deleteChronicle(baseUrl, id) {
    return this.request(`/chronicles/${encodeURIComponent(id)}`, { baseUrl, method: "DELETE" });
  }

  listChronicleRecipients(baseUrl) {
    return this.request("/chronicles/recipients", { baseUrl });
  }

  async getChronicleAsset(baseUrl, id, url) {
    const match = String(url).match(/^\/chronicles\/(\d+)\/assets\/([a-f0-9]{32})$/);
    if (!match || Number(match[1]) !== Number(id)) throw new ShrecknetApiError("Invalid Chronicle image URL.", 0);
    return this.requestAsset(baseUrl, url);
  }

  async getKnowledgeCover(baseUrl, worldId, sourceId) {
    const path = `/shreckworlds/${encodeURIComponent(worldId)}/library-items/${encodeURIComponent(sourceId)}/cover`;
    return this.requestAsset(baseUrl, path);
  }

  async uploadChronicleAsset(baseUrl, id, file) {
    const form = new FormData();
    form.append("file", file);
    const response = await this._fetch(baseUrl, `/chronicles/${encodeURIComponent(id)}/assets`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.accessToken}`, Accept: "application/json" },
      body: form,
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      if (response.ok) {
        throw new ShrecknetApiError("ShreckRPG returned an invalid image-upload response.", response.status, error);
      }
    }
    if (!response.ok) {
      const detail = payload?.detail;
      const message = typeof detail === "string" ? detail : detail?.message || detail?.code || "Chronicle image upload failed.";
      throw new ShrecknetApiError(message, response.status, payload);
    }
    if (!payload || payload.url !== `/chronicles/${id}/assets/${payload.id}` || !/^[a-f0-9]{32}$/.test(payload.id)) {
      throw new ShrecknetApiError("ShreckRPG returned an invalid Chronicle image.", response.status);
    }
    return payload;
  }
}
