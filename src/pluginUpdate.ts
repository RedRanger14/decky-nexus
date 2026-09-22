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
  /** The release body, as published. Markdown, and possibly empty. */
  notes?: string;
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

/** How long to leave the button saying "waiting" before letting it be
 * pressed again.
 *
 * Decky's confirmation prompt can be cancelled and nothing tells us when
 * it was, so the only way back is a clock. Without one the button stayed
 * disabled for good.
 *
 * Six seconds: Michael cancelled a prompt and found twenty-five too long
 * to sit there. The prompt itself appears in about one, so this only has
 * to cover that. The cost of being brief is that the button goes live
 * again a few seconds into a real install, which is harmless: the poll
 * below flips it to "Updated" as soon as the new version is running.
 */
export const UPDATE_WAIT_MS = 6_000;

/** Whether the update request has visibly finished.
 *
 * "Finished" means the running version is now at least the one that was
 * offered. Checked rather than assumed, because after Decky installs the
 * update it restarts the plugin, and this page belongs to the instance
 * being replaced: it cannot rely on being told.
 */
export function updateLanded(offered: string, running?: string): boolean {
  if (!offered || !running) return false;
  return !isNewerVersion(running, offered);
}

/** Release notes, flattened into lines a panel can show.
 *
 * Deliberately not a Markdown renderer. It strips the handful of marks
 * the release notes actually use, keeps headings and bullets legible as
 * text, and drops the trailing legal line and the install instructions,
 * which are the two sections nobody reading an update prompt needs.
 */
export function formatReleaseNotes(md?: string, maxLines = 40): string[] {
  if (!md) return [];
  const out: string[] = [];
  // Everything before the first heading is the release notes' standing
  // preamble: what the plugin is, and that it needs Premium. True, and
  // worthless to somebody who already has it installed and is looking at
  // an update prompt. It also filled the whole collapsed view, pushing
  // the actual changes behind "Show more".
  let skipping = /^##+\s/m.test(md);
  for (const raw of md.replace(/\r/g, "").split("\n")) {
    const line = raw.trimEnd();
    if (/^##+\s/.test(line)) {
      const heading = line.replace(/^##+\s*/, "").trim();
      // The two sections that are noise here: the reader is already
      // installing it, and the footer is boilerplate.
      skipping = /^(installing|supported games)$/i.test(heading);
      if (!skipping) out.push(heading.toUpperCase());
      continue;
    }
    if (skipping) continue;
    if (/^---+$/.test(line)) break; // the footer rule ends the useful part
    const text = line
      .replace(/^[-*]\s+/, "- ")
      .replace(/\*\*(.+?)\*\*/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .trim();
    if (!text) {
      if (out.length && out[out.length - 1] !== "") out.push("");
      continue;
    }
    out.push(text);
    if (out.length >= maxLines) break;
  }
  while (out.length && out[out.length - 1] === "") out.pop();
  return out;
}
