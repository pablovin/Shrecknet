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
  game.settings.register(MODULE_ID, "connectionStatus", {
    scope: "world", config: false, restricted: true, type: String, default: "unknown",
  });
  game.settings.register(MODULE_ID, "connectionCheckedAt", {
    scope: "world", config: false, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "themeId", {
    name: "Shrecknet theme",
    scope: "world", config: true, restricted: true, type: String, default: "terminal",
    choices: { terminal: "Shrecknet Terminal" },
  });
  game.settings.registerMenu(MODULE_ID, "connection", {
    name: "Shrecknet Connection",
    label: "Configure Shrecknet",
    hint: "Add the one-time Shrecknet service key and select the linked Shrecknet World.",
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
    status: game.settings.get(MODULE_ID, "connectionStatus"),
    checkedAt: game.settings.get(MODULE_ID, "connectionCheckedAt"),
    themeId: game.settings.get(MODULE_ID, "themeId"),
  };
}

export async function saveConnectionState({ serverUrl, worldId, worldName }) {
  if (!game.user.isGM) throw new Error("Only a Foundry GM can configure Shrecknet.");
  await game.settings.set(MODULE_ID, "serverUrl", serverUrl);
  await game.settings.set(MODULE_ID, "worldId", worldId);
  await game.settings.set(MODULE_ID, "worldName", worldName);
  await setConnectionStatus("online");
}

export async function setConnectionStatus(status) {
  if (!game.user.isGM) throw new Error("Only a Foundry GM can update Shrecknet connection status.");
  await game.settings.set(MODULE_ID, "connectionStatus", status);
  await game.settings.set(MODULE_ID, "connectionCheckedAt", new Date().toISOString());
}
