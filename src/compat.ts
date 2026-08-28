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
// Mods whose in-game UI only answers a mouse.
//
// A handheld running Gaming Mode has a gamepad and nothing else. A mod that
// draws a window with a button you have to CLICK is not merely awkward there,
// it can strand you: Palworld's Mod Config Menu (UI) opens a "Finish Setup"
// dialog the first time a mod that uses it loads, and on a controller there
// is no way to press the button, no way to close the window and no way to
// reach the mod's own fallbacks behind it.
//
// This is deliberately a list of FRAMEWORKS, not of mods. Mod Config Menu is
// a config interface other authors build against, so the mods that inherit
// the problem are exactly the mods that list it as a Nexus requirement - and
// we already download those requirements. Naming the framework once therefore
// covers every mod that uses it, including ones published after this line was
// written, which a per-mod list never would.
export interface MouseOnlyMod {
  nexusDomain: string;
  modId: number;
  /** How the mod is named to the player. */
  name: string;
  /** What actually happens on a controller. */
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

export const MOUSE_ONLY_MODS: MouseOnlyMod[] = [
  {
    nexusDomain: "palworld",
    modId: 577, // Mod Config Menu (UI)
    name: "Mod Config Menu (UI)",
    effect:
      "It opens a setup window the first time it loads, and its buttons " +
      "only answer a mouse. On a controller the window cannot be closed, " +
      "which leaves you stuck in the game (verified 2026-08-28).",
    undeclaredUsers: [
      703, // Creative Menu: declares no Nexus requirements whatsoever
    ],
  },
];

function findMouseOnly(
  nexusDomain: string,
  modId: number
): MouseOnlyMod | undefined {
  return MOUSE_ONLY_MODS.find(
    (m) => m.nexusDomain === nexusDomain && m.modId === modId
  );
}

/** Which mouse-only frameworks a collection pulls in.
 *
 * Matched against the collection's own mod-id list, which we already have -
 * no per-mod requirement lookup, which at 100+ mods would be far too slow to
 * run before the install button. A collection that includes a mod configured
 * through one of these frameworks includes the framework too, so the id list
 * is enough to catch it. */
export function collectionMouseOnly(
  nexusDomain: string,
  modIds: number[]
): MouseOnlyMod[] {
  const ids = new Set(modIds);
  return MOUSE_ONLY_MODS.filter(
    (m) =>
      m.nexusDomain === nexusDomain &&
      (ids.has(m.modId) ||
        (m.undeclaredUsers ?? []).some((u) => ids.has(u)))
  );
}

/** Warning text when this mod needs a mouse - because it IS one of the
 * mouse-only frameworks, because it lists one as a requirement, or because
 * it is a known user of one that declares no requirements at all.
 *
 * `requirements` may be undefined while the page is still loading; that only
 * costs us the inherited case, and the direct case still reports. */
export function getControllerWarning(
  nexusDomain: string,
  modId: number,
  requirements?: { modId: number; modName: string }[]
): string | undefined {
  const direct = findMouseOnly(nexusDomain, modId);
  if (direct) {
    return (
      `${direct.name} ${direct.effect} Install it only if you can attach a ` +
      `mouse, or use Steam's own pointer (hold the STEAM button and use the ` +
      `right trackpad, STEAM and the right trigger to click).`
    );
  }
  const reqIds = (requirements ?? []).map((r) => r.modId);
  for (const fw of MOUSE_ONLY_MODS) {
    if (fw.nexusDomain !== nexusDomain) continue;
    const via =
      reqIds.includes(fw.modId) || (fw.undeclaredUsers ?? []).includes(modId)
        ? fw
        : undefined;
    if (via) {
      return (
        `This mod is configured through ${via.name}, which it requires. ` +
        `${via.effect} The mod itself may work, but its settings are out of ` +
        `reach on a controller alone.`
      );
    }
  }
  return undefined;
}
