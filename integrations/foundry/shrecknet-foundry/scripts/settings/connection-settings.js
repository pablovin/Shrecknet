export const MODULE_ID = "shrecknet-foundry";

export function registerConnectionSettings(ConnectionSettingsApplication) {
  game.settings.register(MODULE_ID, "serverUrl", {
    scope: "world", config: false, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "worldId", {
    scope: "world", config: false, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "worldName", {
    scope: "world", config: false, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "shreckWorldUrl", {
    scope: "world", config: false, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "shreckRpgUrl", {
    scope: "world", config: false, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "connectionStatus", {
    scope: "world", config: false, restricted: true, type: String, default: "unknown",
  });
  game.settings.register(MODULE_ID, "connectionCheckedAt", {
    scope: "world", config: false, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "themeId", {
    name: "Shrecknet theme",
    scope: "world", config: true, restricted: true, type: String, default: "terminal",
    choices: { terminal: "ShreckOS" },
  });
  game.settings.registerMenu(MODULE_ID, "connection", {
    name: "Shrecknet Connection",
    label: "Configure Shrecknet",
    hint: "Configure the Shrecknet World and the connected ShreckWorld and ShreckRPG APIs.",
    icon: "shrecknet-logo-icon",
    type: ConnectionSettingsApplication,
    restricted: true,
  });
}

export function getConnectionState() {
  return {
    serverUrl: game.settings.get(MODULE_ID, "serverUrl"),
    worldId: game.settings.get(MODULE_ID, "worldId"),
    worldName: game.settings.get(MODULE_ID, "worldName"),
    shreckWorldUrl: game.settings.get(MODULE_ID, "shreckWorldUrl"),
    shreckRpgUrl: game.settings.get(MODULE_ID, "shreckRpgUrl"),
    status: game.settings.get(MODULE_ID, "connectionStatus"),
    checkedAt: game.settings.get(MODULE_ID, "connectionCheckedAt"),
    themeId: game.settings.get(MODULE_ID, "themeId"),
  };
}

export async function saveConnectionState({ serverUrl, worldId, worldName, shreckWorldUrl, shreckRpgUrl }) {
  if (!game.user.isGM) throw new Error("Only a Foundry GM can configure Shrecknet.");
  await game.settings.set(MODULE_ID, "serverUrl", serverUrl);
  await game.settings.set(MODULE_ID, "worldId", worldId);
  await game.settings.set(MODULE_ID, "worldName", worldName);
  await game.settings.set(MODULE_ID, "shreckWorldUrl", normalizeSavedServiceUrl(shreckWorldUrl));
  await game.settings.set(MODULE_ID, "shreckRpgUrl", normalizeSavedServiceUrl(shreckRpgUrl));
  await setConnectionStatus("online");
}

export async function saveServiceUrls({ shreckWorldUrl, shreckRpgUrl }) {
  if (!game.user.isGM) throw new Error("Only a Foundry GM can configure Shrecknet service URLs.");
  await game.settings.set(MODULE_ID, "shreckWorldUrl", normalizeSavedServiceUrl(shreckWorldUrl));
  await game.settings.set(MODULE_ID, "shreckRpgUrl", normalizeSavedServiceUrl(shreckRpgUrl));
}

function normalizeSavedServiceUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

export async function setConnectionStatus(status) {
  if (!game.user.isGM) throw new Error("Only a Foundry GM can update Shrecknet connection status.");
  await game.settings.set(MODULE_ID, "connectionStatus", status);
  await game.settings.set(MODULE_ID, "connectionCheckedAt", new Date().toISOString());
}
