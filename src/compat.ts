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
