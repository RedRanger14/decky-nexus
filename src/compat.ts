// Curated platform-compatibility hints for specific mods - knowledge that
// Nexus's requirements system can't express (e.g. dependencies that are only
// mandatory on the native Linux build). Seeded from verified findings; the
// long-term home for this is a community/Nexus-side signal, not a hardcoded
// list.

export interface CompatHint {
  nexusDomain: string;
  modId: number;
  hint: string;
}

export const COMPAT_HINTS: CompatHint[] = [
  {
    nexusDomain: "slaythespire2",
    modId: 854, // Ironclad Skin-Crimson Blade Valkyrie
    hint:
      "On the Linux/SteamOS build this mod additionally requires RitsuLib — " +
      "without it, its startup patching crashes and the skin never loads " +
      "(Windows is unaffected; verified 2026-07-16). Install RitsuLib first.",
  },
];

export function getCompatHint(
  nexusDomain: string,
  modId: number
): string | undefined {
  return COMPAT_HINTS.find(
    (h) => h.nexusDomain === nexusDomain && h.modId === modId
  )?.hint;
}

// ---------------------------------------------------------------------------
// Mods whose in-game UI traps the player in Gaming Mode.
//
// Palworld's Mod Config Menu (UI) opens a "Finish Setup" dialog the first
// time a mod that uses it loads, and under Gaming Mode NO input reaches it:
// controller, keyboard and trackpad pointer were all tried on device and
// none could press the button or close the window (2026-08-28). It is not
// a missing-mouse problem - it is a window nothing can dismiss, with the
// mod's own fallbacks unreachable behind it.
//
// This is deliberately a list of FRAMEWORKS, not of mods. Mod Config Menu is
// a config interface other authors build against, so the mods that inherit
// the problem are exactly the mods that list it as a Nexus requirement - and
// we already download those requirements. Naming the framework once therefore
// covers every mod that uses it, including ones published after this line was
// written, which a per-mod list never would.
export interface StrandingUiMod {
  nexusDomain: string;
  modId: number;
  /** How the mod is named to the player. */
  name: string;
  /** What actually happens in Gaming Mode. */
  effect: string;
  /** Mods that use this framework without declaring it as a Nexus
   * requirement, so the requirements list cannot find them.
   *
   * Creative Menu is the one that caught us: it is configured entirely
   * through Mod Config Menu and declares no requirements at all, so from a
   * collection it is caught (the collection carries the framework) but from
   * its own page it would have installed silently. Curated, and only ever
   * from a mod we have actually watched do this. */
  undeclaredUsers?: number[];
}

export const STRANDING_UI_MODS: StrandingUiMod[] = [
  {
    nexusDomain: "palworld",
    modId: 577, // Mod Config Menu (UI)
    name: "Mod Config Menu (UI)",
    effect:
      "It opens a setup window the first time it loads, and in Gaming " +
      "Mode no input reaches that window: controller, keyboard and mouse " +
      "all do nothing, so it cannot be closed and you are locked out of " +
      "the game (verified 2026-08-28).",
    undeclaredUsers: [
      703, // Creative Menu: declares no Nexus requirements whatsoever
    ],
  },
];

function findStrandingUi(
  nexusDomain: string,
  modId: number
): StrandingUiMod | undefined {
  return STRANDING_UI_MODS.find(
    (m) => m.nexusDomain === nexusDomain && m.modId === modId
  );
}

/** Which stranding-UI frameworks a collection pulls in.
 *
 * Matched against the collection's own mod-id list, which we already have -
 * no per-mod requirement lookup, which at 100+ mods would be far too slow to
 * run before the install button. A collection that includes a mod configured
 * through one of these frameworks includes the framework too, so the id list
 * is enough to catch it. */
export function collectionStrandingUi(
  nexusDomain: string,
  modIds: number[]
): StrandingUiMod[] {
  const ids = new Set(modIds);
  return STRANDING_UI_MODS.filter(
    (m) =>
      m.nexusDomain === nexusDomain &&
      (ids.has(m.modId) ||
        (m.undeclaredUsers ?? []).some((u) => ids.has(u)))
  );
}

/** Which of a collection's mods should be installed SWITCHED OFF.
 *
 * These are the mods that open the stranding window themselves (the
 * undeclaredUsers of a stranding-UI framework), not the framework: Mod
 * Config Menu sat inert through a whole collection until Creative Menu
 * registered with it, so the framework alone is safe to leave on.
 * Installing rather than skipping keeps the collection complete for someone
 * who also plays on desktop - they switch the mod on in My Mods and lose
 * nothing. */
export function collectionAutoOff(
  nexusDomain: string,
  modIds: number[]
): { modId: number; via: StrandingUiMod }[] {
  const ids = new Set(modIds);
  const out: { modId: number; via: StrandingUiMod }[] = [];
  for (const fw of STRANDING_UI_MODS) {
    if (fw.nexusDomain !== nexusDomain) continue;
    for (const u of fw.undeclaredUsers ?? []) {
      if (ids.has(u)) out.push({ modId: u, via: fw });
    }
  }
  return out;
}

/** Warning text when this mod can trap the player - because it IS one of
 * the stranding-UI frameworks, because it lists one as a requirement, or
 * because it is a known user of one that declares no requirements at all.
 *
 * `requirements` may be undefined while the page is still loading; that only
 * costs us the inherited case, and the direct case still reports. */
export function getStrandingWarning(
  nexusDomain: string,
  modId: number,
  requirements?: { modId: number; modName: string }[]
): string | undefined {
  const direct = findStrandingUi(nexusDomain, modId);
  if (direct) {
    return (
      `${direct.name} ${direct.effect} Only install it if you also play ` +
      `this game on a desktop, where its window can be set up once and ` +
      `put away.`
    );
  }
  const reqIds = (requirements ?? []).map((r) => r.modId);
  for (const fw of STRANDING_UI_MODS) {
    if (fw.nexusDomain !== nexusDomain) continue;
    const via =
      reqIds.includes(fw.modId) || (fw.undeclaredUsers ?? []).includes(modId)
        ? fw
        : undefined;
    if (via) {
      return (
        `This mod is configured through ${via.name}, which it requires. ` +
        `${via.effect} The mod itself may work, but its settings are out ` +
        `of reach in Gaming Mode.`
      );
    }
  }
  return undefined;
}
