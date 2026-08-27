// Shared update-scan logic: the QAM badge and the full-screen Updates
// page both use it (scoped to one game or across all).
import {
  checkFrameworkUpdate,
  checkUpdates,
  getBlamedFolders,
  getInstalledMods,
} from "./api";
import { ALL_GAMES, SupportedGame, modeParams } from "./games";

export interface PendingUpdate {
  game: SupportedGame;
  folder: string;
  modId: number;
  name: string;
  current: string;
  /** A script extender rather than a mod: it has no install record, and its
   * right build is the one matching the game's exe, not the newest. Applied
   * by re-running the framework install, which picks by game version. */
  framework?: boolean;
  /** A framework's version currently ON DISK. `current` keeps its meaning
   * everywhere - the version being offered - so this is the other half. */
  installedVersion?: string;
}

export async function scanUpdates(
  scopedGame?: SupportedGame
): Promise<PendingUpdate[]> {
  const found: PendingUpdate[] = [];
  for (const game of scopedGame ? [scopedGame] : ALL_GAMES) {
    // A collection pins its mods on purpose, so they are normally left out
    // of update checks. One the game blamed for errors has forfeited that -
    // the badge should not stay silent about the fix for a crash.
    const blamed =
      game.logAdapter?.kind === "godot"
        ? await getBlamedFolders(
            game.nexusDomain,
            game.installDirName,
            game.modsSubdir,
            game.logAdapter.userDirName
          )
            .then((b) => (b.ok ? b.folders ?? [] : []))
            .catch(() => [])
        : [];
    const [mods, updates] = await Promise.all([
      getInstalledMods(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        ...modeParams(game),
        game.protectedModFolders ?? []
      ),
      checkUpdates(game.nexusDomain, blamed),
    ]);
    // The script extender everything else depends on. Checked separately
    // because it writes no install record, so checkUpdates cannot see it.
    if (game.framework?.nexusModId && game.framework.detectFile) {
      const fw = await checkFrameworkUpdate(
        game.nexusDomain,
        game.framework.nexusModId,
        game.installDirName,
        game.framework.detectFile,
        game.processName ?? "",
        game.framework.avoidFileKeywords ?? []
      ).catch(() => ({ ok: false }) as { ok: boolean });
      if ("update_available" in fw && fw.update_available) {
        found.push({
          game,
          folder: `framework:${game.framework.nexusModId}`,
          modId: game.framework.nexusModId,
          name: game.framework.name,
          // The build that matches this game's exe, which after a
          // deliberate downgrade is OLDER than what is installed.
          current: fw.target_version || "",
          framework: true,
          installedVersion: fw.installed_version || "",
        });
      }
    }
    if (!updates.ok || !updates.updates) continue;
    const byFolder = new Map((mods.mods ?? []).map((m) => [m.folder, m]));
    for (const [folder, info] of Object.entries(updates.updates)) {
      const mod = byFolder.get(folder);
      if (info.update_available && mod?.mod_id) {
        found.push({
          game,
          folder,
          modId: mod.mod_id,
          name: mod.name ?? folder,
          current: info.current,
        });
      }
    }
  }
  return found;
}
