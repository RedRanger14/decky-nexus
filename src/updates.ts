// Shared update-scan logic: the QAM badge and the full-screen Updates
// page both use it (scoped to one game or across all).
import { checkUpdates, getInstalledMods } from "./api";
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
    const [mods, updates] = await Promise.all([
      getInstalledMods(
        game.nexusDomain,
        game.installDirName,
        game.modsSubdir,
        ...modeParams(game),
        game.protectedModFolders ?? []
      ),
      checkUpdates(game.nexusDomain),
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
