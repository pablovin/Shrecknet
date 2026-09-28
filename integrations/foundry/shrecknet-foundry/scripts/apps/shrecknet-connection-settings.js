import { ShrecknetShell } from "./shrecknet-shell.js";

/**
 * GM-only world-connection configuration window. It reuses the shell's setup
 * actions, but has a dedicated template so the regular control never exposes
 * the service-key form to players.
 */
export class ShrecknetConnectionSettings extends ShrecknetShell {
  static DEFAULT_OPTIONS = {
    ...ShrecknetShell.DEFAULT_OPTIONS,
    id: "shrecknet-connection-settings",
    window: {
      title: "Shrecknet Connection",
      icon: "shrecknet-logo-icon",
      resizable: false,
    },
    position: { width: 560, height: 500 },
  };

  static PARTS = {
    main: { template: "modules/shrecknet-foundry/templates/connection-settings.hbs" },
  };
}
