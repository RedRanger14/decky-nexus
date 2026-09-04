// Stranding-UI mod detection. Run via: pnpm run test:compat
// (compiles src/compat.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  COLLECTION_OFF_MODS,
  COMPAT_HINTS,
  STRANDING_UI_MODS,
  collectionAutoOff,
  collectionStrandingUi,
  getCompatHint,
  getStrandingWarning,
} from "../.test-build/compat.js";

// Palworld's Mod Config Menu (UI). Michael, 2026-08-28, after a 108-mod
// collection put him in a game he could not get out of: "the controller and
// my connected keyboard with trackpad do nothing and I cant close the pop
// up" - then, on the touchscreen suggestion, "I dont want to use the
// touchscreen because this plugin is for steamos and there will be plently
// of devices running steamos or bazzite that have no touch screen".
const MOD_CONFIG_MENU = 577;
const CREATIVE_MENU = 703; // the mod he actually got stuck behind
const CREATIVE_MENU_LIKE = 999901; // a mod that merely REQUIRES the above

test("the stranding list names the framework, not its dependents", () => {
  const pal = STRANDING_UI_MODS.filter((m) => m.nexusDomain === "palworld");
  assert.ok(
    pal.some((m) => m.modId === MOD_CONFIG_MENU),
    "Mod Config Menu (UI) must be listed"
  );
  for (const m of STRANDING_UI_MODS) {
    assert.ok(m.name.length > 0, "every entry needs a player-facing name");
    assert.ok(
      m.effect.length > 20,
      "every entry must say what actually happens in Gaming Mode"
    );
  }
});

test("the framework itself warns with no requirement lookup at all", () => {
  const w = getStrandingWarning("palworld", MOD_CONFIG_MENU);
  assert.ok(w, "must warn without being handed requirements");
  assert.match(w, /cannot be closed/);
  assert.match(w, /locked out of the game/);
});

test("a mod inherits the warning through its Nexus requirements", () => {
  const w = getStrandingWarning("palworld", CREATIVE_MENU_LIKE, [
    { modId: 1234, modName: "Some Other Thing" },
    { modId: MOD_CONFIG_MENU, modName: "Mod Config Menu (UI)" },
  ]);
  assert.ok(w, "a mod configured through the framework must warn too");
  assert.match(w, /Mod Config Menu/);
});

// Checked live against the Nexus API on 2026-08-28: Creative Menu declares
// NO nexusRequirements at all, so the requirements path finds nothing. From
// a collection it is still caught (the collection carries the framework),
// but from its own page it would have installed in silence.
test("a known user that declares no requirements is still caught", () => {
  const w = getStrandingWarning("palworld", CREATIVE_MENU, []);
  assert.ok(w, "Creative Menu must warn even with an empty requirements list");
  assert.match(w, /Mod Config Menu/);
  // And with requirements never having loaded at all.
  assert.ok(getStrandingWarning("palworld", CREATIVE_MENU, undefined));
});

test("a collection warns on a known user even without the framework", () => {
  const hits = collectionStrandingUi("palworld", [1, CREATIVE_MENU, 2]);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].modId, MOD_CONFIG_MENU);
  // ...and does not double-report when the collection carries both.
  assert.equal(
    collectionStrandingUi("palworld", [CREATIVE_MENU, MOD_CONFIG_MENU]).length,
    1
  );
});

test("an unrelated mod is never warned about", () => {
  assert.equal(getStrandingWarning("palworld", CREATIVE_MENU_LIKE, []), undefined);
  assert.equal(
    getStrandingWarning("palworld", CREATIVE_MENU_LIKE, [
      { modId: 1234, modName: "Some Other Thing" },
    ]),
    undefined
  );
  // Same id, different game: ids are per-domain and must not leak across.
  assert.equal(getStrandingWarning("skyrimspecialedition", MOD_CONFIG_MENU), undefined);
});

test("requirements still loading does not crash or fabricate a warning", () => {
  assert.equal(getStrandingWarning("palworld", CREATIVE_MENU_LIKE, undefined), undefined);
  // ...but the direct case must not WAIT for requirements to arrive.
  assert.ok(getStrandingWarning("palworld", MOD_CONFIG_MENU, undefined));
});

test("a collection is judged from its own mod-id list", () => {
  const hits = collectionStrandingUi("palworld", [1, 2, MOD_CONFIG_MENU, 3]);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].modId, MOD_CONFIG_MENU);
  assert.deepEqual(collectionStrandingUi("palworld", [1, 2, 3]), []);
  assert.deepEqual(collectionStrandingUi("stardewvalley", [MOD_CONFIG_MENU]), []);
});

// Michael, 2026-08-28, after tapping OK was ruled out on every input he
// had: "can we disable that mod by default so people dont have that
// experience when they install collections". Installed but OFF - someone
// with a mouse flips it on in My Mods and loses nothing.
test("a collection switches the stranding mod off, not the framework", () => {
  const off = collectionAutoOff("palworld", [1, CREATIVE_MENU, MOD_CONFIG_MENU]);
  assert.equal(off.length, 1);
  assert.equal(off[0].modId, CREATIVE_MENU, "Creative Menu goes off");
  assert.match(off[0].reason, /cannot be closed/);
  // The framework alone strands nobody: it sat inert through a whole
  // collection until a mod registered with it. It stays on.
  assert.deepEqual(collectionAutoOff("palworld", [MOD_CONFIG_MENU]), []);
  assert.deepEqual(collectionAutoOff("palworld", [1, 2, 3]), []);
  assert.deepEqual(collectionAutoOff("stardewvalley", [CREATIVE_MENU]), []);
});

// One of a Kind - Pal Variant Overhaul (524): loads after every reskin by
// design (the zzzz folder name) and swaps Pal looks at runtime, so in a
// reskin-heavy collection every affected Pal rendered as a broken mix of
// the two. A/B verified on device 2026-08-28: off = every reskin correct,
// with the reinstalled single-variant FOMOD mods confirmed applied.
test("a mod that fights the rest of a collection goes in switched off", () => {
  const ONE_OF_A_KIND = 524;
  const off = collectionAutoOff("palworld", [1, ONE_OF_A_KIND, 2]);
  assert.equal(off.length, 1);
  assert.equal(off[0].modId, ONE_OF_A_KIND);
  assert.match(off[0].reason, /reskins/i);
  // Per-domain, like everything else in this file.
  assert.deepEqual(collectionAutoOff("stardewvalley", [ONE_OF_A_KIND]), []);
  // Its own mod page is untouched: alone, outside a reskin collection,
  // the mod is fine - this is a collection-context rule only.
  assert.equal(getStrandingWarning("palworld", ONE_OF_A_KIND), undefined);
});

test("both auto-off sources merge into one list", () => {
  const ONE_OF_A_KIND = 524;
  const off = collectionAutoOff("palworld", [CREATIVE_MENU, ONE_OF_A_KIND]);
  assert.deepEqual(
    off.map((o) => o.modId).sort((a, b) => a - b),
    [ONE_OF_A_KIND, CREATIVE_MENU].sort((a, b) => a - b)
  );
  for (const o of off) assert.ok(o.reason.length > 20, "every entry says why");
});

// The whole point of this feature is that it fires BEFORE the download, so
// the pages that can install must actually consult it. A version of this
// shipped where the rule existed and nothing called it would be worthless.
test("the mod page and the collection page both consult the rule", () => {
  const mod = readFileSync("src/ModDetailPage.tsx", "utf8");
  assert.match(
    mod,
    /getStrandingWarning\(/,
    "ModDetailPage must call getStrandingWarning"
  );
  assert.match(
    mod,
    /strandingWarning &&/,
    "ModDetailPage must render the warning it computed"
  );
  const coll = readFileSync("src/CollectionPage.tsx", "utf8");
  assert.match(
    coll,
    /collectionStrandingUi\(/,
    "CollectionPage must call collectionStrandingUi"
  );
  assert.match(
    coll,
    /collectionAutoOff\(/,
    "CollectionPage must compute which mods to switch off"
  );
  assert.match(
    coll,
    /toggleMod\(game, m\.folder, false\)/,
    "CollectionPage must actually switch them off via the choke point"
  );
  assert.match(
    coll,
    /autoOffNote\(/,
    "CollectionPage must tell the user what it switched off"
  );
});

// Michael's standing rule, and the reason the copy is checked at all: the
// answer must never be "use the touchscreen", because the target hardware
// is every SteamOS and Bazzite device, most of which have no touchscreen.
test("no advice anywhere tells a handheld user to tap the screen", () => {
  const sources = [
    readFileSync("src/compat.ts", "utf8"),
    readFileSync("src/CollectionPage.tsx", "utf8"),
  ].join("\n");
  assert.doesNotMatch(sources, /touchscreen|touch screen/i);
});

// No Press Any Key Menu (745), Baldur's Gate 3. Michael: "I am at the bg3
// menu but I cant even click the mod manager option, the controller and
// other options are working". Isolated over five boots of the 235-mod NG+
// collection on 2026-09-04: off = the entry opens, on = the entry is dead.
// The mod ships GUI/Pages/BetterMainMenu.xaml and both input state
// machines, so it replaces the menu rather than tweaking it, and its copy
// predates the Mod Manager entry the current build puts there.
const NO_PRESS_ANY_KEY = 745;
const IMPUI = 366; // overrides menu files too, and was CLEARED by the A/B

test("the menu-replacing BG3 mod goes into a collection switched off", () => {
  const off = collectionAutoOff("baldursgate3", [1, NO_PRESS_ANY_KEY, IMPUI]);
  assert.equal(off.length, 1, "only the mod the boots actually convicted");
  assert.equal(off[0].modId, NO_PRESS_ANY_KEY);
  assert.match(off[0].reason, /Mod Manager/);
  // ImpUI was on for the boot that worked. Overriding menu files is not
  // itself the fault, so it must never be swept up by association.
  assert.deepEqual(collectionAutoOff("baldursgate3", [IMPUI]), []);
  assert.deepEqual(collectionAutoOff("palworld", [NO_PRESS_ANY_KEY]), []);
});

test("its own mod page says so before the download", () => {
  const note = getCompatHint("baldursgate3", NO_PRESS_ANY_KEY);
  assert.ok(note, "a direct install must be warned too");
  assert.match(note.hint, /Mod Manager/);
  // Not dressed up as a Linux problem: it replaces the menu everywhere,
  // and a reader told "Linux note" would assume their desktop is fine.
  assert.doesNotMatch(note.label ?? "", /Linux/i);
  assert.doesNotMatch(note.hint, /Linux|SteamOS/i);
  // The genuinely Linux-only hint keeps the default framing.
  const linux = getCompatHint("slaythespire2", 854);
  assert.match(linux.hint, /Linux/);
  assert.equal(linux.label, undefined, "no label means the Linux default");
  assert.equal(getCompatHint("baldursgate3", 999999), undefined);
});

test("the mod page renders each note's own heading, not a hardcoded one", () => {
  const mod = readFileSync("src/ModDetailPage.tsx", "utf8");
  assert.match(mod, /compatHint\.hint/, "must render the hint text");
  assert.match(mod, /compatHint\.label \?\? "Linux note"/);
  assert.match(mod, /compatHint\.icon \?\? "🐧"/);
});

// No em dashes in player-facing copy, wherever it lives.
test("the warning copy carries no em dashes", () => {
  for (const m of STRANDING_UI_MODS) {
    assert.doesNotMatch(m.name + m.effect, /—/);
  }
  for (const m of COLLECTION_OFF_MODS) {
    assert.doesNotMatch(m.name + m.reason, /—/);
  }
  for (const h of COMPAT_HINTS) {
    assert.doesNotMatch(h.hint + (h.label ?? ""), /—/);
  }
  const w = getStrandingWarning("palworld", MOD_CONFIG_MENU);
  assert.doesNotMatch(w, /—/);
});
