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

  async request(path, { method = "GET", body, integrationKey, authenticated = true } = {}) {
    if (!this.serverUrl) throw new ShrecknetApiError("A Shrecknet server URL is required.", 0);
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (integrationKey) headers["X-Shrecknet-Integration-Key"] = integrationKey;
    if (authenticated && this.accessToken) headers.Authorization = `Bearer ${this.accessToken}`;

    let response;
    try {
      response = await fetch(`${this.serverUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (_) {
      throw new ShrecknetApiError("Unable to connect to Shrecknet.", 0);
    }

    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      const detail = payload?.detail;
      const message = typeof detail === "string" ? detail : detail?.message || "Shrecknet request failed.";
      throw new ShrecknetApiError(message, response.status, payload);
    }
    return payload;
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
}
