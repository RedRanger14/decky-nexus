/** Updating the plugin itself, from Gaming Mode.
 *
 * The README said for months that this was impossible because Decky's
 * plugin folder is owned by root. That confused "this plugin cannot
 * write it" with "it cannot happen". Decky Loader owns that folder and
 * runs as root, and it exposes installation over its own websocket
 * router as `utilities/install_plugin`, which is exactly the route its
 * store uses. So the plugin cannot install itself, but it can ask the
 * thing that can, and the user confirms in Decky's own prompt.
 *
 * What the loader does once asked (backend/decky_loader/browser.py):
 * downloads the artifact, refuses it on a SHA-256 mismatch, unzips it
 * into the plugin folder and fixes permissions. Nothing here needs a
 * password, Desktop Mode, or a terminal.
 */

/** Decky's own install kinds. The loader's prompt reads from this, so
 * sending UPDATE is what makes it say update rather than install.
 * backend/decky_loader/browser.py: PluginInstallType. */
export const DECKY_INSTALL_TYPE_UPDATE = 2;

export interface PluginUpdate {
  ok: boolean;
  current: string;
  update_available: boolean;
  version?: string;
  artifact?: string;
  hash?: string;
}

/** The loader's websocket router, as the loader's own frontend uses it.
 *
 * Deliberately typed loosely and reached through `window`: this is an
 * internal of Decky rather than part of @decky/api, so it may not be
 * there. Every caller must cope with that instead of assuming it.
 */
interface DeckyRouter {
  call<T = unknown>(route: string, ...args: unknown[]): Promise<T>;
}

export function deckyRouter(): DeckyRouter | undefined {
  const be = (window as unknown as { DeckyBackend?: DeckyRouter }).DeckyBackend;
  return typeof be?.call === "function" ? be : undefined;
}

/** Can this device update the plugin from Gaming Mode at all?
 *
 * If Decky ever renames the route or drops the global, the honest
 * answer is the manual instructions, not a button that does nothing.
 */
export function canSelfUpdate(): boolean {
  return Boolean(deckyRouter());
}

/** "1.11.2" -> [1, 11, 2]. Anything unparsable counts as 0 so a bad
 * version string cannot throw inside a render. */
export function versionParts(v: string): number[] {
  return String(v ?? "")
    .trim()
    .replace(/^[vV]/, "")
    .split(".")
    .slice(0, 4)
    .map((p) => {
      const m = /^\d+/.exec(p);
      return m ? parseInt(m[0], 10) : 0;
    });
}

/** Strictly newer, never equal and never older.
 *
 * A string compare puts 1.9.9 above 1.11.0, which would have offered a
 * downgrade the day this shipped. It also matters for dev builds: the
 * device often runs a version ahead of the store, and it must not be
 * nagged to go back.
 */
export function isNewerVersion(current: string, latest: string): boolean {
  if (!latest) return false;
  const a = versionParts(current);
  const b = versionParts(latest);
  const len = Math.max(a.length, b.length, 3);
  for (let i = 0; i < len; i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (y !== x) return y > x;
  }
  return false;
}

/** The artifact must come from this project's own releases.
 *
 * The loader installs whatever URL it is handed, so this is the last
 * point where that is checked on our side. The backend checks it too;
 * both, because it is the one field that decides what code runs.
 */
export function trustedArtifact(url?: string): boolean {
  return Boolean(
    url?.startsWith(
      "https://github.com/RedRanger14/decky-nexus/releases/download/"
    )
  );
}

export function pluginUpdateLabel(u?: PluginUpdate): string {
  if (!u?.update_available || !u.version) return "";
  return `Plugin update: v${u.version}`;
}

export function pluginUpdateBody(u?: PluginUpdate): string {
  if (!u?.update_available || !u.version) return "";
  return (
    `You have v${u.current}. Decky will ask you to confirm, then ` +
    `download and install it. Your mods, API key and settings are not ` +
    `touched.`
  );
}

/** Reasons this cannot proceed, as something a person can act on. "" when
 * it can. */
export function pluginUpdateBlocked(u?: PluginUpdate): string {
  if (!u?.update_available) return "";
  if (!trustedArtifact(u.artifact) || !u.hash) {
    return "The update could not be verified, so it was not offered.";
  }
  if (!canSelfUpdate()) {
    return (
      "This build of Decky cannot install plugins from here. Update from " +
      "Desktop Mode instead, as the README describes."
    );
  }
  return "";
}

/** Ask Decky Loader to install the newer build.
 *
 * Returns "" once the request is with Decky, which then shows its own
 * confirmation prompt. Returning early does NOT mean the update
 * happened: the user still has to confirm, and Decky restarts the
 * plugin afterwards.
 */
export async function requestPluginUpdate(u: PluginUpdate): Promise<string> {
  const blocked = pluginUpdateBlocked(u);
  if (blocked) return blocked;
  const router = deckyRouter();
  if (!router) return "Decky's installer is not available on this build.";
  if (!u.version || !u.artifact || !u.hash) return "Nothing to install.";
  try {
    await router.call(
      "utilities/install_plugin",
      u.artifact,
      // Must match the installed folder name, or Decky installs a second
      // copy beside this one rather than replacing it.
      "Nexus Mods",
      u.version,
      u.hash,
      DECKY_INSTALL_TYPE_UPDATE
    );
    return "";
  } catch (e) {
    return `Decky refused the update: ${String(e)}`;
  }
}
