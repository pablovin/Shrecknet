import { ShrecknetClient } from "../api/shrecknet-client.js";
import { getConnectionState, setConnectionStatus } from "./connection-settings.js";

const HEALTH_CHECK_INTERVAL_MS = 5 * 60 * 1000;
let monitorId = null;

/** Check the saved World connection without requiring the one-time service key. */
export async function checkConfiguredConnection() {
  const connection = getConnectionState();
  if (!connection.serverUrl || !connection.worldId) {
    throw new Error("Select a Shrecknet World before checking the connection.");
  }

  const client = new ShrecknetClient({ serverUrl: connection.serverUrl });
  try {
    await client.ping();
    await setConnectionStatus("online");
    return "online";
  } catch (error) {
    await setConnectionStatus("offline");
    throw error;
  }
}

/** A GM refreshes the shared World status while this Foundry client is open. */
export function startConnectionMonitor() {
  if (!game.user.isGM || monitorId !== null) return;
  const check = () => checkConfiguredConnection().catch(() => undefined);
  check();
  monitorId = globalThis.setInterval(check, HEALTH_CHECK_INTERVAL_MS);
}
