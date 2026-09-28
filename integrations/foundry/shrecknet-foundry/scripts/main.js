import { ShrecknetShell } from "./apps/shrecknet-shell.js";
import { registerConnectionSettings } from "./settings/connection-settings.js";

Hooks.once("init", () => {
  registerConnectionSettings();
  console.info("[Shrecknet] Foundry module initialized");
});

Hooks.on("getSceneControlButtons", (controls) => {
  controls.shrecknet ??= {
    name: "shrecknet",
    title: "Shrecknet",
    icon: "fa-solid fa-terminal",
    order: 900,
    tools: {},
  };
  controls.shrecknet.tools.open = {
    name: "open",
    title: "Open Shrecknet",
    icon: "fa-solid fa-network-wired",
    order: 1,
    button: true,
    visible: true,
    onChange: () => new ShrecknetShell().render({ force: true }),
  };
});
