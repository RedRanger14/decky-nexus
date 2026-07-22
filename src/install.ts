// Shared install path used by the mod detail page, the requirements
// batch installer, and the Updates section - so every entry point routes
// a mod through the identical per-game pipeline.
import {
  InstallResult,
  getModFiles,
  installFomod,
  installMod,
} from "./api";
import { SupportedGame, modeParams } from "./games";
import { nameDownload } from "./state";

/** Complete a FOMOD install after the wizard. */
export async function finishFomod(
  token: string,
  selectedIds: string[]
): Promise<InstallResult> {
  return installFomod(token, selectedIds);
}

/** Install a SPECIFIC pinned file (collections pin exact file ids).
 * Same pipeline, same Downloads-panel tracking. */
export async function installPinned(
  game: SupportedGame,
  modId: number,
  fileId: number,
  fileName: string,
  modName: string,
  version = ""
): Promise<InstallResult> {
  nameDownload(modId, modName);
  return installModWith(game, modId, fileId, fileName, modName, version, "collection");
}

function installModWith(
  game: SupportedGame,
  modId: number,
  fileId: number,
  fileName: string,
  modName: string,
  version: string,
  source: string,
  pageVersion = ""
): Promise<InstallResult> {
  return installMod(
    game.nexusDomain,
    modId,
    fileId,
    fileName,
    modName,
    version,
    game.installDirName,
    game.modsSubdir,
    "",
    "",
    ...modeParams(game),
    "",
    game.ue4ss?.modsSubdir ?? "",
    game.ue4ss?.logicModsSubdir ?? "",
    game.launcherXmlSubpath ?? "",
    game.flatModExtensions ?? [],
    pageVersion,
    source
  );
}

/** Install a mod's primary (latest main) file through the full pipeline.
 * Registers the download so the QAM Downloads panel tracks it. Returns
 * the InstallResult (needs_choice archives are surfaced to the caller). */
export async function installLatest(
  game: SupportedGame,
  modId: number,
  modName: string,
  pageVersion = ""
): Promise<InstallResult> {
  const files = await getModFiles(game.nexusDomain, modId);
  const file = files.files?.[0];
  if (!file) {
    return { ok: false, error: "No downloadable file found" };
  }
  nameDownload(modId, modName);
  return installModWith(
    game,
    modId,
    file.file_id,
    file.file_name,
    modName,
    file.version || pageVersion,
    "",
    pageVersion
  );
}
