// Shared install path used by the mod detail page, the requirements
// batch installer, and the Updates section - so every entry point routes
// a mod through the identical per-game pipeline.
import {
  installFrostyMod,
  setFrostyModEnabled,
  setModEnabled,
  uninstallFrostyMod,
  uninstallMod,
  InstallResult,
  getModFiles,
  installFomod,
  installMod,
  prefetchModFile,
  prepareModFile,
} from "./api";
import { SupportedGame, modeParams, stalenessExemptModIds } from "./games";
import { nameDownload } from "./state";

/** Download AND extract a pinned file so the installer only has to
 * commit it. Extraction is the CPU-bound half of an install and shares
 * nothing, so running it for the next mods while the current one commits
 * is free wall-clock. The commit itself stays serial and in order. */
export async function preparePinned(
  game: SupportedGame,
  modId: number,
  fileId: number,
  fileName: string,
  modName: string
): Promise<void> {
  nameDownload(modId, modName, game.appId);
  try {
    await prepareModFile(game.nexusDomain, modId, fileId, fileName);
  } catch {
    // Best-effort: the installer does the work itself and owns the
    // error reporting for this mod's row.
  }
}

/** Download a pinned file into the backend's archive cache ahead of its
 * serial install - the collection pipeline runs several of these in
 * parallel so the network never idles while other mods extract. */
export async function prefetchPinned(
  game: SupportedGame,
  modId: number,
  fileId: number,
  fileName: string,
  modName: string
): Promise<void> {
  nameDownload(modId, modName, game.appId);
  try {
    await prefetchModFile(game.nexusDomain, modId, fileId, fileName);
  } catch {
    // Best-effort: the installer retries the download itself and
    // surfaces the real error on the mod's own row.
  }
}

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
  version = "",
  collectionSlug = "",
  payloadChoice = "",
  /** Restore missing files only - never overwrite what's on disk. Used
   * by the collection repair pass; see installModWith. */
  repairOnly = false
): Promise<InstallResult> {
  nameDownload(modId, modName, game.appId);
  return installModWith(
    game,
    modId,
    fileId,
    fileName,
    modName,
    version,
    "collection",
    "",
    collectionSlug,
    payloadChoice,
    repairOnly
  );
}

/** The ONE place that decides how a mod gets installed for a given game.
 *
 * Exported so pages call this instead of the api underneath it. The mod page
 * once duplicated this branch and My Mods skipped it entirely, and both times
 * Battlefront II quietly took the folder path. */
export function installModWith(
  game: SupportedGame,
  modId: number,
  fileId: number,
  fileName: string,
  modName: string,
  version: string,
  source: string,
  pageVersion = "",
  collectionSlug = "",
  payloadChoice = "",
  repairOnly = false
): Promise<InstallResult> {
  if (game.frostbite) {
    // Frostbite games compile rather than copy: one call that converts the
    // mod and recompiles the enabled set. Routed here, at the single point
    // every install path already passes through, so the mod page, the
    // collection flow and Update all all get it without their own branch.
    return installFrostyMod(
      game.nexusDomain,
      modId,
      fileId,
      fileName,
      modName,
      version,
      game.installDirName,
      game.appId,
      pageVersion,
      payloadChoice
    );
  }

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
    payloadChoice,
    game.ue4ss?.modsSubdir ?? "",
    game.ue4ss?.logicModsSubdir ?? "",
    game.launcherXmlSubpath ?? "",
    game.flatModExtensions ?? [],
    pageVersion,
    source,
    game.witcherLayout ?? false,
    collectionSlug,
    game.cp77Layout ?? false,
    game.pakPatchLayout ?? false,
    repairOnly,
    // Loaders are exempt from the built-for-an-older-patch rule: they load
    // other dlls rather than patching game code, so a game update does not
    // age them out.
    stalenessExemptModIds(game),
    game.hd2Layout ?? false,
    game.reshade?.subdir ?? ""
  );
}

/** Install a mod's primary (latest main) file through the full pipeline.
 * Registers the download so the QAM Downloads panel tracks it. Returns
 * the InstallResult (needs_choice archives are surfaced to the caller). */
export async function installLatest(
  game: SupportedGame,
  modId: number,
  modName: string,
  pageVersion = "",
  payloadChoice = ""
): Promise<InstallResult> {
  const files = await getModFiles(game.nexusDomain, modId);
  const file = files.files?.[0];
  if (!file) {
    return { ok: false, error: "No downloadable file found" };
  }
  nameDownload(modId, modName, game.appId);
  return installModWith(
    game,
    modId,
    file.file_id,
    file.file_name,
    modName,
    file.version || pageVersion,
    "",
    pageVersion,
    "",
    payloadChoice
  );
}

/** Toggle a mod, whichever mechanism the game uses.
 *
 * Frostbite games have no per-mod switch: enabling or disabling anything
 * recompiles the whole enabled set, which takes a minute or two. Everything
 * else moves a folder. Callers should not have to know which.
 */
export async function toggleMod(
  game: SupportedGame,
  folder: string,
  enabled: boolean
): Promise<{ ok: boolean; error?: string }> {
  if (game.frostbite) {
    return setFrostyModEnabled(
      game.nexusDomain,
      folder,
      enabled,
      game.installDirName,
      game.appId
    );
  }

  return setModEnabled(
    game.installDirName,
    game.modsSubdir,
    folder,
    enabled,
    game.installMode ?? "folder",
    game.nexusDomain,
    game.appId,
    game.pluginsTxtSubpath ?? "",
    game.pluginsTxtStyle ?? "starred"
  );
}

/** Remove a mod, whichever mechanism the game uses. */
export async function removeMod(
  game: SupportedGame,
  folder: string
): Promise<{ ok: boolean; error?: string }> {
  if (game.frostbite) {
    return uninstallFrostyMod(
      game.nexusDomain,
      folder,
      game.installDirName,
      game.appId
    );
  }

  return uninstallMod(
    game.nexusDomain,
    game.installDirName,
    game.modsSubdir,
    folder,
    game.installMode ?? "folder",
    game.appId,
    game.pluginsTxtSubpath ?? "",
    game.pluginsTxtStyle ?? "starred"
  );
}
