// Thin wrappers around the Steam client globals Decky exposes.
import { Router } from "@decky/ui";

export function isGameRunning(appId: number): boolean {
  const app = Router.MainRunningApp;
  return app !== undefined && Number(app.appid) === appId;
}

function gameIdFor(appId: number): string {
  try {
    const overview = (globalThis as any).appStore?.GetAppOverviewByAppID?.(appId);
    if (overview?.m_gameid) return String(overview.m_gameid);
  } catch {
    // fall through to plain appid
  }
  return String(appId);
}

async function waitFor(pred: () => boolean, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (pred()) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return pred();
}

/** App id of the library game page currently on screen, if any.
 * (Gaming Mode routes game pages as /library/app/<appid>.) */
export function getViewedLibraryAppId(): number | undefined {
  try {
    const path = (Router as any).WindowStore?.GamepadUIMainWindowInstance
      ?.BrowserWindow?.location?.pathname as string | undefined;
    const match = path?.match(/\/library\/app\/(\d+)/);
    return match ? Number(match[1]) : undefined;
  } catch {
    return undefined;
  }
}

export function getAppDisplayName(appId: number): string | undefined {
  try {
    return (globalThis as any).appStore?.GetAppOverviewByAppID?.(appId)
      ?.display_name;
  } catch {
    return undefined;
  }
}

/** Terminate the game if running, wait for it to exit, then launch it. */
export async function restartGame(appId: number): Promise<boolean> {
  const steamClient = (globalThis as any).SteamClient;
  if (!steamClient?.Apps?.RunGame) return false;
  const gameId = gameIdFor(appId);
  if (isGameRunning(appId)) {
    steamClient.Apps.TerminateApp(gameId, false);
    const closed = await waitFor(() => !Router.MainRunningApp, 20000);
    if (!closed) return false;
    // grace period so Steam finishes tearing the session down
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  steamClient.Apps.RunGame(gameId, "", -1, 100);
  return true;
}
