// Mouse-only mod detection. Run via: pnpm run test:compat
// (compiles src/compat.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  MOUSE_ONLY_MODS,
  collectionMouseOnly,
  getControllerWarning,
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

test("the mouse-only list names the framework, not its dependents", () => {
  const pal = MOUSE_ONLY_MODS.filter((m) => m.nexusDomain === "palworld");
  assert.ok(
    pal.some((m) => m.modId === MOD_CONFIG_MENU),
    "Mod Config Menu (UI) must be listed"
  );
  for (const m of MOUSE_ONLY_MODS) {
    assert.ok(m.name.length > 0, "every entry needs a player-facing name");
    assert.ok(
      m.effect.length > 20,
      "every entry must say what actually happens on a controller"
    );
  }
});

test("the framework itself warns with no requirement lookup at all", () => {
  const w = getControllerWarning("palworld", MOD_CONFIG_MENU);
  assert.ok(w, "must warn without being handed requirements");
  assert.match(w, /mouse/i);
});

test("a mod inherits the warning through its Nexus requirements", () => {
  const w = getControllerWarning("palworld", CREATIVE_MENU_LIKE, [
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
  const w = getControllerWarning("palworld", CREATIVE_MENU, []);
  assert.ok(w, "Creative Menu must warn even with an empty requirements list");
  assert.match(w, /Mod Config Menu/);
  // And with requirements never having loaded at all.
  assert.ok(getControllerWarning("palworld", CREATIVE_MENU, undefined));
});

test("a collection warns on a known user even without the framework", () => {
  const hits = collectionMouseOnly("palworld", [1, CREATIVE_MENU, 2]);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].modId, MOD_CONFIG_MENU);
  // ...and does not double-report when the collection carries both.
  assert.equal(
    collectionMouseOnly("palworld", [CREATIVE_MENU, MOD_CONFIG_MENU]).length,
    1
  );
});

test("an unrelated mod is never warned about", () => {
  assert.equal(getControllerWarning("palworld", CREATIVE_MENU_LIKE, []), undefined);
  assert.equal(
    getControllerWarning("palworld", CREATIVE_MENU_LIKE, [
      { modId: 1234, modName: "Some Other Thing" },
    ]),
    undefined
  );
  // Same id, different game: ids are per-domain and must not leak across.
  assert.equal(getControllerWarning("skyrimspecialedition", MOD_CONFIG_MENU), undefined);
});

test("requirements still loading does not crash or fabricate a warning", () => {
  assert.equal(getControllerWarning("palworld", CREATIVE_MENU_LIKE, undefined), undefined);
  // ...but the direct case must not WAIT for requirements to arrive.
  assert.ok(getControllerWarning("palworld", MOD_CONFIG_MENU, undefined));
});

test("a collection is judged from its own mod-id list", () => {
  const hits = collectionMouseOnly("palworld", [1, 2, MOD_CONFIG_MENU, 3]);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].modId, MOD_CONFIG_MENU);
  assert.deepEqual(collectionMouseOnly("palworld", [1, 2, 3]), []);
  assert.deepEqual(collectionMouseOnly("stardewvalley", [MOD_CONFIG_MENU]), []);
});

// The whole point of this feature is that it fires BEFORE the download, so
// the pages that can install must actually consult it. A version of this
// shipped where the rule existed and nothing called it would be worthless.
test("the mod page and the collection page both consult the rule", () => {
  const mod = readFileSync("src/ModDetailPage.tsx", "utf8");
  assert.match(
    mod,
    /getControllerWarning\(/,
    "ModDetailPage must call getControllerWarning"
  );
  assert.match(
    mod,
    /controllerWarning &&/,
    "ModDetailPage must render the warning it computed"
  );
  const coll = readFileSync("src/CollectionPage.tsx", "utf8");
  assert.match(
    coll,
    /collectionMouseOnly\(/,
    "CollectionPage must call collectionMouseOnly"
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
  for (const m of MOUSE_ONLY_MODS) {
    assert.doesNotMatch(m.name + m.effect, /—/);
  }
  const w = getControllerWarning("palworld", MOD_CONFIG_MENU);
  assert.doesNotMatch(w, /—/);
});
