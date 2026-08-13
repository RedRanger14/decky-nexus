// Shared update-scan logic: the QAM badge and the full-screen Updates
// page both use it (scoped to one game or across all).
import { checkUpdates, getBlamedFolders, getInstalledMods } from "./api";
import { ALL_GAMES, SupportedGame, modeParams } from "./games";

export interface PendingUpdate {
  game: SupportedGame;
  folder: string;
  modId: number;
  name: string;
  current: string;
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
