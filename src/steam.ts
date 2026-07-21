// Thin wrappers around the Steam client globals Decky exposes.
import { Router } from "@decky/ui";

/** All currently running app ids (Steam supports several at once). */
export function getRunningAppIds(): number[] {
  try {
    const apps = (Router as any).RunningApps as { appid: string }[] | undefined;
    if (apps && apps.length > 0) return apps.map((a) => Number(a.appid));
  } catch {
    // fall through to MainRunningApp
  }
  const main = Router.MainRunningApp;
  return main ? [Number(main.appid)] : [];
}

export function isGameRunning(appId: number): boolean {
  return getRunningAppIds().includes(appId);
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

/** Current route path of the main Gaming Mode window, trying the locations
 * different Steam client versions expose it at. */
export function getMainWindowPath(): string | undefined {
  try {
    const router = Router as any;
    const win = router.WindowStore?.GamepadUIMainWindowInstance;
    return (
      win?.m_history?.location?.pathname ??
      router.m_history?.location?.pathname ??
      win?.BrowserWindow?.location?.pathname ??
      undefined
    );
  } catch {
    return undefined;
  }
}

/** App id of the library game page currently on screen, if any.
 * (Gaming Mode routes game pages as /library/app/<appid>.) */
export function getViewedLibraryAppId(): number | undefined {
  const match = getMainWindowPath()?.match(/\/library\/app\/(\d+)/);
  return match ? Number(match[1]) : undefined;
}

export function getAppDisplayName(appId: number): string | undefined {
  try {
    return (globalThis as any).appStore?.GetAppOverviewByAppID?.(appId)
      ?.display_name;
  } catch {
    return undefined;
  }
}

/** Set a Steam game's launch options (e.g. the SMAPI wrapper command).
 * Returns false if the client API isn't available. */
export function setLaunchOptions(appId: number, options: string): boolean {
  try {
    const apps = (globalThis as any).SteamClient?.Apps;
    if (!apps?.SetAppLaunchOptions) return false;
    apps.SetAppLaunchOptions(appId, options);
    return true;
  } catch {
    return false;
  }
}

/** Force a Steam Play compatibility tool (e.g. "proton_experimental") for
 * a game - how we swap a native-Linux install to the Windows build that
 * mod loaders need. Returns false if the client API isn't available. */
export function setCompatTool(appId: number, toolName: string): boolean {
  try {
    const apps = (globalThis as any).SteamClient?.Apps;
    if (!apps?.SpecifyCompatTool) return false;
    apps.SpecifyCompatTool(appId, toolName);
    return true;
  } catch {
    return false;
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
