export const MODULE_ID = "shrecknet-foundry";

export function registerConnectionSettings() {
  game.settings.register(MODULE_ID, "serverUrl", {
    name: "Shrecknet server URL",
    hint: "The base URL of the Shrecknet API, for example https://shrecknet.example.",
    scope: "world", config: true, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "worldId", {
    scope: "world", config: false, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "worldName", {
    scope: "world", config: false, restricted: true, type: String, default: "",
  });
  game.settings.register(MODULE_ID, "themeId", {
    name: "Shrecknet theme",
    scope: "world", config: true, restricted: true, type: String, default: "terminal",
    choices: { terminal: "Shrecknet Terminal" },
  });
}

export function getConnectionState() {
  return {
    serverUrl: game.settings.get(MODULE_ID, "serverUrl"),
    worldId: game.settings.get(MODULE_ID, "worldId"),
    worldName: game.settings.get(MODULE_ID, "worldName"),
    themeId: game.settings.get(MODULE_ID, "themeId"),
  };
}

export async function saveConnectionState({ serverUrl, worldId, worldName }) {
  if (!game.user.isGM) throw new Error("Only a Foundry GM can configure Shrecknet.");
  await game.settings.set(MODULE_ID, "serverUrl", serverUrl);
  await game.settings.set(MODULE_ID, "worldId", worldId);
  await game.settings.set(MODULE_ID, "worldName", worldName);
}
