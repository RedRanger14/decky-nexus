// Stranding-UI mod detection. Run via: pnpm run test:compat
// (compiles src/compat.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  STRANDING_UI_MODS,
  collectionAutoOff,
  collectionStrandingUi,
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
  assert.equal(off[0].via.modId, MOD_CONFIG_MENU, "because of the framework");
  // The framework alone strands nobody: it sat inert through a whole
  // collection until a mod registered with it. It stays on.
  assert.deepEqual(collectionAutoOff("palworld", [MOD_CONFIG_MENU]), []);
  assert.deepEqual(collectionAutoOff("palworld", [1, 2, 3]), []);
  assert.deepEqual(collectionAutoOff("stardewvalley", [CREATIVE_MENU]), []);
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
    /strandingOffNote\(/,
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

// No em dashes in player-facing copy.
test("the warning copy carries no em dashes", () => {
  for (const m of STRANDING_UI_MODS) {
    assert.doesNotMatch(m.name + m.effect, /—/);
  }
  const w = getStrandingWarning("palworld", MOD_CONFIG_MENU);
  assert.doesNotMatch(w, /—/);
});
