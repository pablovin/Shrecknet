import { ShrecknetShell } from "./apps/shrecknet-shell.js";
import { ShrecknetConnectionSettings } from "./apps/shrecknet-connection-settings.js";
import { registerConnectionSettings } from "./settings/connection-settings.js";

Hooks.once("init", () => {
  registerConnectionSettings(ShrecknetConnectionSettings);
  console.info("[Shrecknet] Foundry module initialized");
});

Hooks.on("getSceneControlButtons", (controls) => {
  controls.shrecknet ??= {
    name: "shrecknet",
    title: "Shrecknet",
    icon: "shrecknet-logo-icon",
    order: 900,
    tools: {},
  };
  controls.shrecknet.tools.open = {
    name: "open",
    title: "Open Shrecknet",
    icon: "shrecknet-logo-icon",
    order: 1,
    button: true,
    visible: true,
    onChange: () => new ShrecknetShell().render({ force: true }),
  };
});
