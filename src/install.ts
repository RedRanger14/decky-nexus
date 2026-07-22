// Shared install path used by the mod detail page, the requirements
// batch installer, and the Updates section - so every entry point routes
// a mod through the identical per-game pipeline.
import {
  InstallResult,
  getModFiles,
  installMod,
} from "./api";
import { SupportedGame, modeParams } from "./games";
import { nameDownload } from "./state";

/** Install a mod's primary (latest main) file through the full pipeline.
 * Registers the download so the QAM Downloads panel tracks it. Returns
 * the InstallResult (needs_choice archives are surfaced to the caller). */
export async function installLatest(
  game: SupportedGame,
  modId: number,
  modName: string,
  fallbackVersion = ""
): Promise<InstallResult> {
  const files = await getModFiles(game.nexusDomain, modId);
  const file = files.files?.[0];
  if (!file) {
    return { ok: false, error: "No downloadable file found" };
  }
  nameDownload(modId, modName);
  return installMod(
    game.nexusDomain,
    modId,
    file.file_id,
    file.file_name,
    modName,
    file.version || fallbackVersion,
    game.installDirName,
    game.modsSubdir,
    "",
    "",
    ...modeParams(game),
    "",
    game.ue4ss?.modsSubdir ?? "",
    game.ue4ss?.logicModsSubdir ?? "",
    game.launcherXmlSubpath ?? "",
    game.flatModExtensions ?? []
  );
}
