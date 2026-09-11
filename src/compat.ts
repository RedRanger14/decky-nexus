// Curated platform-compatibility hints for specific mods - knowledge that
// Nexus's requirements system can't express (e.g. dependencies that are only
// mandatory on the native Linux build). Seeded from verified findings; the
// long-term home for this is a community/Nexus-side signal, not a hardcoded
// list.

export interface CompatHint {
  nexusDomain: string;
  modId: number;
  hint: string;
  /** How the note is headed, and the emoji beside it. Defaults to the
   * Linux framing the first entry needed. A mod that misbehaves on every
   * platform must not present itself as a Linux problem: the reader would
   * reasonably conclude it works fine on their desktop. */
  label?: string;
  icon?: string;
}

export const COMPAT_HINTS: CompatHint[] = [
  {
    nexusDomain: "slaythespire2",
    modId: 854, // Ironclad Skin-Crimson Blade Valkyrie
    hint:
      "On the Linux/SteamOS build this mod additionally requires RitsuLib. " +
      "Without it, its startup patching crashes and the skin never loads " +
      "(Windows is unaffected; verified 2026-07-16). Install RitsuLib first.",
  },
  {
    nexusDomain: "baldursgate3",
    modId: 745, // No Press Any Key Menu
    // Not a Linux fault: it replaces the menu on every platform, and the
    // entry its copy cannot reach is the game's own.
    label: "Heads up",
    icon: "⚠️",
    hint:
      "This mod replaces the game's main menu with its own copy, and in " +
      "that copy the Mod Manager option cannot be selected: the menu " +
      "appears, every other option works, and Mod Manager does nothing. " +
      "Isolated on this device on 2026-09-04, switching only this mod off " +
      "and on across five boots of a 223 mod setup. Everything else about " +
      "the mod works, so the choice is skipping the press any key screen " +
      "or keeping the game's own mod list.",
  },
];

export function getCompatHint(
  nexusDomain: string,
  modId: number
): CompatHint | undefined {
  return COMPAT_HINTS.find(
    (h) => h.nexusDomain === nexusDomain && h.modId === modId
  );
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

// ---------------------------------------------------------------------------
// Mods a collection installs switched off because they fight the rest of it.
//
// A different failure class from the stranding UIs: these load fine and
// take input fine, but override the same assets as the mods around them.
// Curated, and only ever from a combination watched failing on device.
export interface CollectionOffMod {
  nexusDomain: string;
  modId: number;
  name: string;
  /** Why it goes in switched off, shown to the player with the note. */
  reason: string;
  /** Only in these collections (slugs). A mod that works everywhere else
   * and collides with one particular curator's load order must not be
   * switched off for people installing something else. Absent means the
   * rule applies to every collection carrying the mod. */
  collections?: string[];
}

export const COLLECTION_OFF_MODS: CollectionOffMod[] = [
  {
    nexusDomain: "palworld",
    modId: 524, // One of a Kind - Pal Variant Overhaul
    name: "One of a Kind - Pal Variant Overhaul",
    // The 2nd most popular collection: ~100 per-Pal reskins plus this,
    // which names itself zzzz... to load after all of them and swaps Pal
    // looks at runtime per individual. The two composite into broken
    // chimera renders - modded face on a vanilla body, an alpha boss as
    // a half-textured blend. With it off, every reskin showed correctly
    // (A/B on device, 2026-08-28).
    reason:
      "It changes how Pals look at runtime, on top of every other mod, " +
      "so in a collection full of Pal reskins the two fight and Pals " +
      "render as a broken mix of both (seen on this device). With it " +
      "off, the collection's reskins show as their authors intended.",
  },
  {
    nexusDomain: "baldursgate3",
    modId: 745, // No Press Any Key Menu
    name: "No Press Any Key Menu",
    // It ships GUI/Pages/BetterMainMenu.xaml plus both input state
    // machines, so it does not tweak the menu, it replaces it - and its
    // copy predates the Mod Manager entry the current build puts there.
    // Isolated over five boots of the 235-mod NG+ collection on
    // 2026-09-04: with it off the entry opens, with it on the entry is
    // dead while every other menu option and the controller work.
    // ImpUI overrides menu files too and is unaffected.
    reason:
      "It replaces the game's main menu with its own copy, and in that " +
      "copy the Mod Manager option cannot be selected, so you lose the " +
      "game's own mod list (isolated on this device). With it off the " +
      "Mod Manager works, and the only difference is that you see the " +
      "press any key screen again.",
  },
  {
    nexusDomain: "baldursgate3",
    modId: 4964, // Astralities' Glow Eyes
    name: "Astralities' Glow Eyes",
    // Scoped to one collection on purpose. This mod works in New Game
    // Plus on the same device. In Difficulty, Immersion, Quality (866
    // mods) its copy of the character visual bank - 4,620 references to
    // humanoid body visuals inside an eye mod - collides with something
    // else in that load order, and every custom character of a humanoid
    // race loses its body while Dragonborn and premade companions are
    // fine. Isolated 2026-09-06 by switching this one group off and on
    // across the full collection. Demon Eyes and Feywild Eyes require it
    // and go off with it (the backend cascades from the stored reason).
    collections: ["pns4qv"],
    reason:
      "In this collection it collides with another mod over how custom " +
      "characters' bodies are drawn, and every humanoid race loses its " +
      "arms, hands and body (isolated on this device). The mod itself " +
      "works in other collections, so it is only switched off here.",
  },
  {
    nexusDomain: "baldursgate3",
    modId: 17706, // Goon's Monk Overhaul
    name: "Goon's Monk Overhaul",
    // Every other Goon's overhaul is caught by the dependency pass, because
    // its Nexus page lists Goons Library, which needs the Script Extender
    // and is parked on the native Linux build. This one's page lists
    // NOTHING, and its pak declares no dependencies either, so no rule can
    // see the link. Convicted by bisection 2026-09-06: with it on, the
    // game dies 16 seconds into boot, in any collection where the library
    // cannot run - which on this platform is every collection.
    reason:
      "It builds on Goons Library, which needs the BG3 Script Extender " +
      "and cannot run on the native Linux build. Its page does not list " +
      "that requirement, so nothing else can catch it, and with it on the " +
      "game crashes before the menu (isolated on this device).",
  },
  {
    nexusDomain: "baldursgate3",
    modId: 18772, // Circle of Witchcraft - A Druid subclass
    name: "Circle of Witchcraft - A Druid subclass",
    // Convicted 2026-09-05 on a healthy device: added alone to 113 mods
    // that reached the menu, the game hung; its three alphabetical
    // neighbours each booted in the same session. It lists no requirement
    // anywhere, so the mechanism is unknown and the rule stays in the
    // collection it was seen in.
    //
    // Eleven further "crash" convictions from 2026-09-06 were shipped in
    // 1.6.4 and withdrawn in 1.6.9: they were made while the device had
    // begun crashing at every size regardless of content (the same 522-mod
    // set booted at 17:11 and 20:33 and crashed twice at 22:20), so a
    // bisection on that base convicted whichever mod sat in the losing
    // half. A verdict without a healthy base is noise with a name on it.
    collections: ["pns4qv"],
    reason:
      "With this mod on, the game does not reach the menu in this " +
      "collection (isolated on this device by switching mods off and on). " +
      "It may work in other collections, so it is only switched off here.",
  },
  // The two below are the strongest evidence in this file, and the method
  // is worth copying. Both were found on 2026-09-10 by a group hunt over
  // the 450 registered mods of the #2 collection (DUNGEON), 37 boots: a
  // group of 32 boots and everyone in it is cleared, a group that hangs is
  // halved, and a single mod is only ever convicted after booting it ALONE
  // with nothing else registered. Both of these hang there, stuck at the
  // game's LoadModule stage, while their neighbours reach the menu in the
  // same session. Nothing scoped, because "alone" is not a collection
  // context: the mod does this wherever it is.
  {
    nexusDomain: "baldursgate3",
    modId: 11862, // Yogurt's Wearable Dyes
    name: "Yogurt's Wearable Dyes",
    // Also the mod the prefix bisection pointed at independently, before
    // the alone-test confirmed it. Declares no dependencies in its pak and
    // none on its page, so no dependency rule can see whatever it needs.
    reason:
      "With this mod on, the game stops part-way through loading and never " +
      "reaches the menu. Isolated on this device by starting the game with " +
      "this as the only mod switched on, twice. It declares no " +
      "requirements anywhere, so nothing else can catch it.",
  },
  {
    nexusDomain: "baldursgate3",
    modId: 11331, // Vestments of The Apostate
    name: "Vestments of The Apostate",
    reason:
      "With this mod on, the game stops part-way through loading and never " +
      "reaches the menu. Isolated on this device by starting the game with " +
      "this as the only mod switched on. It declares no requirements " +
      "anywhere, so nothing else can catch it.",
  },
  {
    nexusDomain: "baldursgate3",
    modId: 16325, // KAVT - Kazstra's Virtual Tav x Tattoo, Makeup & Scar Extender
    name: "KAVT - Kazstra's Virtual Tav x Tattoo, Makeup & Scar Extender",
    // The appearance framework half of the #2 collection stands on. It
    // builds its textures through a Script Extender patcher (UAP) that
    // cannot exist on the native Linux build, so character creation reaches
    // for a texture slot that was never filled and dies: a null read in a
    // GPU resource job (bg3+0x23b2cc4), the same fault Michael's New Game
    // hit. Found 2026-09-10 by a character-creation hunt driven through
    // the game's own menu: 13 groups of 32 loaded character creation, the
    // group holding this did not, and KAVT ALONE - nothing else registered
    // - crashes it every time, while vanilla loads it fine. Its EotB patch
    // pak carries the Script Extender config and the main pak does not, so
    // a collection pinning only the main pak would install it switched on.
    reason:
      "It builds the tattoos, makeup and scars it adds through a Script " +
      "Extender patcher, which cannot run on the Linux build of the game. " +
      "Without that, character creation crashes the moment it opens " +
      "(isolated on this device with this as the only mod switched on). " +
      "Mods that need it are switched off with it.",
  },
  {
    nexusDomain: "subnautica",
    modId: 984, // Quick Slots Plus (BepInEx)
    name: "Quick Slots Plus (BepInEx)",
    // Michael, 2026-09-11, after installing a 72-mod collection: "when I
    // booted the game, the controller is no longer working in the menu so
    // i cant verify if the mods have loaded. Even my keyboard and trackpad
    // no longer work." The game's own log named it outright:
    //
    //   [Info : BepInEx] Loading [Quick Slots Plus 2.1.1]
    //   [Warning: HarmonyX] Could not find method for type GameInput and
    //                       name Awake
    //   [Error : Unity Log] ArgumentException: Undefined target method for
    //     patch QuickSlotsPlus.Patches.GameInput_Awake_Patch::Postfix()
    //   Rethrow as HarmonyException
    //     QuickSlotsPlus.Mod.Awake ()
    //     BepInEx.Bootstrap.Chainloader:Start()
    //     UnityEngine.InputSystem.InputSystem:.cctor()
    //
    // The bottom of that stack is what makes it total rather than
    // cosmetic: BepInEx's chainloader runs inside InputSystem's STATIC
    // constructor, and an exception there faults the type permanently, so
    // every later input call fails. Switching this one mod off restored
    // the controller. Unscoped: the fault is the game version, not the
    // collection.
    reason:
      "It patches the game's input handler, and the method it looks for " +
      "no longer exists in the current version of the game, so it throws " +
      "while the mod loader is still starting. That happens inside the " +
      "engine's input setup, which leaves the whole input system dead: " +
      "controller, keyboard and trackpad all stop responding at the menu, " +
      "with no way to quit the game from inside it. Isolated on this " +
      "device from the game's own log, and switching it off brought the " +
      "controller straight back.",
  },
];

/** Which of a collection's mods should be installed SWITCHED OFF, each
 * with the reason the player is shown.
 *
 * Two sources. Stranding-UI users (Creative Menu): the mods that open the
 * stranding window themselves, not the framework - Mod Config Menu sat
 * inert through a whole collection until Creative Menu registered with it,
 * so the framework alone is safe to leave on. And COLLECTION_OFF_MODS:
 * mods verified to fight the rest of a collection, some only in a named
 * one. Installing rather than skipping keeps the collection complete -
 * switching one on in My Mods is one tap and loses nothing. */
export function collectionAutoOff(
  nexusDomain: string,
  modIds: number[],
  collectionSlug?: string
): { modId: number; reason: string }[] {
  const ids = new Set(modIds);
  const out: { modId: number; reason: string }[] = [];
  for (const fw of STRANDING_UI_MODS) {
    if (fw.nexusDomain !== nexusDomain) continue;
    for (const u of fw.undeclaredUsers ?? []) {
      if (ids.has(u)) out.push({ modId: u, reason: fw.effect });
    }
  }
  for (const m of COLLECTION_OFF_MODS) {
    if (m.nexusDomain !== nexusDomain || !ids.has(m.modId)) continue;
    if (m.collections && !(collectionSlug && m.collections.includes(collectionSlug))) {
      continue;
    }
    out.push({ modId: m.modId, reason: m.reason });
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
