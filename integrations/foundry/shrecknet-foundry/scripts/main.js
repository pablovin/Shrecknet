import { ShrecknetShell } from "./apps/shrecknet-shell.js";
import { ShrecknetConnectionSettings } from "./apps/shrecknet-connection-settings.js";
import { registerConnectionSettings } from "./settings/connection-settings.js";
import { startConnectionMonitor } from "./settings/connection-monitor.js";

Hooks.once("ready", () => startConnectionMonitor());

Hooks.once("ready", () => {
  game.socket.on("module.shrecknet-foundry", (payload) => {
    if (payload?.type !== "showImage" || !payload.imageUrl) return;
    const ImagePopout = globalThis.ImagePopout || foundry.applications?.apps?.ImagePopout;
    if (ImagePopout) new ImagePopout(payload.imageUrl, { title: payload.title || "Shrecknet" }).render(true);
  });
});

Hooks.once("init", () => {
  registerConnectionSettings(ShrecknetConnectionSettings);
  console.info("[Shrecknet] Foundry module initialized");
});

Hooks.on("getSceneControlButtons", (controls) => {
  controls.shrecknet ??= {
    name: "shrecknet",
    title: "Shrecknet Worlds",
    icon: "shrecknet-logo-icon",
    order: 900,
    tools: {},
  };
  controls.shrecknet.tools.open = {
    name: "open",
    title: "Shrecknet Worlds",
    icon: "shrecknet-logo-icon",
    order: 1,
    button: true,
    visible: true,
    onChange: () => new ShrecknetShell().render({ force: true }),
  };
});
